"""Consumer side: resolve envelope labware to PLR Resources.

Validates that orca-client materializes a PLR Resource from
`CommandMessage.labware` (when set) without consulting any local
labware catalog. Covers PLR-registered labware (`plr_class_name` set),
custom labware (geometry-only reconstruction), and unhappy edges.
"""

import warnings

import pytest
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.resource import Resource

from cheshire_drivers.labware_seed import load_labware_seed
from orca_client.labware.envelope_resolver import (
    EnvelopeLabwareError,
    resolve_envelope_labware,
)
from cheshire_drivers.gateway_protocol import CommandMessage, LabwareDefinitionDTO


# Load once: the seed is ~3.5MB JSON; per-test reload is wasteful.
@pytest.fixture(scope="module")
def seed_entries():
    return load_labware_seed()


def _make_dto_from_seed(entry, **overrides) -> LabwareDefinitionDTO:
    """Build a wire DTO from a cheshire_drivers seed entry."""
    payload = {
        "labware_type": entry.labware_type,
        "display_name": entry.display_name,
        "category": entry.category,
        "vendor": entry.vendor,
        "plr_class_name": entry.plr_class_name,
        "geometry": entry.model_dump(mode="json"),
        "source": "plr_seed",
    }
    payload.update(overrides)
    return LabwareDefinitionDTO.model_validate(payload)


def test_command_message_carries_labware_geometry(seed_entries) -> None:
    """A CommandMessage with `labware` set must round-trip through JSON."""
    plate_seed = next(e for e in seed_entries if e.category == "plate")
    dto = _make_dto_from_seed(plate_seed)
    cmd = CommandMessage(
        command_id="cmd_1",
        device_name="lh_1",
        command="pick_up_tips",
        params={},
        effective_mode="LIVE",
        labware=dto,
    )
    payload = cmd.model_dump_json()
    revived = CommandMessage.model_validate_json(payload)
    assert revived.labware is not None
    assert revived.labware.labware_type == plate_seed.labware_type
    assert revived.labware.geometry["category"] == "plate"


def test_orcaclient_plr_registered_when_plr_class_name_set(seed_entries) -> None:
    """`plr_class_name` set -> resolver calls the canonical PLR factory (PLR-registered path)."""
    plate_seed = next(
        e for e in seed_entries
        if e.category == "plate" and e.plr_class_name == "Cor_96_wellplate_360ul_Fb"
    )
    dto = _make_dto_from_seed(plate_seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resource = resolve_envelope_labware(dto, instance_name="plate_abc")
    assert isinstance(resource, Plate)
    assert resource.name == "plate_abc"
    # Dimensions come from the canonical PLR factory, not the envelope copy.
    assert resource.get_size_x() == pytest.approx(plate_seed.size_x)
    assert resource.get_size_y() == pytest.approx(plate_seed.size_y)


def test_orcaclient_falls_back_to_cd_plates_for_nested_plr_factories(seed_entries) -> None:
    """`plr_class_name` set + not in top-level pylabrobot.resources -> resolver
    falls back to cheshire_drivers.plr.plates and unwraps the adapter wrapper.

    Pinned plate: Cor_Falcon_96_wellplate_340ul_Fb_Black -- referenced by the
    orca-core SMC characterization, lives in pylabrobot.resources nested
    sub-modules, surfaced into the seed by the cheshire-drivers regen
    `cd_plates` walk.
    """
    plate_seed = next(
        e for e in seed_entries
        if e.plr_class_name == "Cor_Falcon_96_wellplate_340ul_Fb_Black"
    )
    dto = _make_dto_from_seed(plate_seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resource = resolve_envelope_labware(dto, instance_name="plate_falcon")
    assert isinstance(resource, Plate)
    assert resource.name == "plate_falcon"
    assert resource.get_size_x() == pytest.approx(plate_seed.size_x)
    assert resource.get_size_y() == pytest.approx(plate_seed.size_y)


def test_orcaclient_custom_labware_when_plr_class_name_null(seed_entries) -> None:
    """`plr_class_name=None` -> resolver reconstructs via PLRLabwareConverter (custom-labware path)."""
    plate_seed = next(e for e in seed_entries if e.category == "plate")
    dto = _make_dto_from_seed(plate_seed, plr_class_name=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resource = resolve_envelope_labware(dto, instance_name="plate_slow")
    assert isinstance(resource, Plate)
    assert resource.name == "plate_slow"
    assert resource.get_size_x() == pytest.approx(plate_seed.size_x)
    assert resource.get_size_y() == pytest.approx(plate_seed.size_y)
    assert resource.num_items_x == plate_seed.num_cols
    assert resource.num_items_y == plate_seed.num_rows


def test_orcaclient_uses_envelope_labware_when_present(seed_entries, monkeypatch) -> None:
    """When envelope labware is set, resolver must NOT call any local catalog.

    Stub `pylabrobot.resources.__getattr__` (the access point for the
    PLR-registered path) to raise; resolver should take the custom-labware
    path when `plr_class_name=None`, succeeding without local lookup.
    """
    plate_seed = next(e for e in seed_entries if e.category == "plate")
    dto = _make_dto_from_seed(plate_seed, plr_class_name=None)

    # Custom-labware reconstruction must succeed even if the local PLR
    # namespace is broken; it must not reach for the PLR-registered path.
    import pylabrobot.resources as plr_resources

    def boom(name):  # noqa: ARG001 - signature shape
        raise RuntimeError("local catalog must not be consulted for custom labware")

    monkeypatch.setattr(plr_resources, "__getattr__", boom, raising=False)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resource = resolve_envelope_labware(dto, instance_name="plate_no_local")
    assert isinstance(resource, Plate)


def test_resolver_raises_on_unknown_plr_class_name(seed_entries) -> None:
    """PLR-registered path with a bogus `plr_class_name` must raise EnvelopeLabwareError."""
    plate_seed = next(e for e in seed_entries if e.category == "plate")
    dto = _make_dto_from_seed(
        plate_seed,
        plr_class_name="this_factory_does_not_exist_in_plr_xyz",
    )
    with pytest.raises(EnvelopeLabwareError, match="not found"):
        resolve_envelope_labware(dto, instance_name="plate_bogus")


def test_resolver_refuses_custom_path_for_non_plate(seed_entries) -> None:
    """Custom-labware reconstruction covers plates only.

    Refusing loudly here keeps the consumer-side surface honest: drivers
    that depend on those categories must wait until the converter grows
    custom-labware support, rather than receiving a half-built resource.
    """
    tip_rack_seed = next(e for e in seed_entries if e.category == "tip_rack")
    dto = _make_dto_from_seed(tip_rack_seed, plr_class_name=None)
    with pytest.raises(EnvelopeLabwareError, match="plate only"):
        resolve_envelope_labware(dto, instance_name="rack_no_custom_path")
