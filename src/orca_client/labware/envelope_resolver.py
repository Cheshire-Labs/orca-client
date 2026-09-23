"""Resolve an envelope-sourced `LabwareDefinitionDTO` into a PLR Resource.

Used by command handlers when `CommandMessage.labware` is set. The
catalog distinguishes two kinds of labware, each resolved differently:

* **PLR-registered labware** (`plr_class_name` set): call the canonical
  PLR factory by name, passing the command's identifier. PLR resources are
  factory functions, not classes, so `getattr(plr_resources, name)(name=...)`
  is the universal call shape.
  Most entries live at the top of `pylabrobot.resources`; a minority
  live in nested PLR sub-modules and are surfaced via the curated
  `cheshire_drivers.plr.plates` wrappers. For those, the wrapper returns a
  `PLR*Adapter` whose inner Resource is
  extracted with `_unwrap_adapter`.
* **Custom labware** (`plr_class_name` is None, `source='operator_custom'`):
  re-validate the geometry blob as a
  `cheshire_drivers.labware_seed.PlateSeedEntry`, which already
  satisfies `_HasPlateGeometry`. Hand it to
  `PLRLabwareConverter.to_plr_plate` for reconstruction.

The legacy local-catalog code path is preserved: if a driver does NOT
read `cmd.labware`, nothing changes. The envelope is the mechanism; a
driver consumes it only once it has been migrated to.

Lookup ordering: top-level `pylabrobot.resources` is tried first; on
miss, `cheshire_drivers.plr.plates` is tried. A top-level entry that
exists but is non-callable or returns a non-`Resource` fails loud and
never falls through -- a same-named cd_plates wrapper cannot shadow a
broken top-level entry. This is intentional: drift in PLR is a real
error, not a fallback condition.
"""

from cheshire_drivers.labware_seed import PlateSeedEntry
from cheshire_drivers.plr import Resource, plr_resources
from cheshire_drivers.plr import plates as cd_plates
from cheshire_drivers.plr.labware_converter import PLRLabwareConverter

from cheshire_drivers.gateway_protocol import LabwareDefinitionDTO


class EnvelopeLabwareError(RuntimeError):
    """Raised when envelope-sourced labware cannot be resolved.

    Distinct from generic LookupError / RuntimeError so callers can
    surface "envelope unresolvable" cleanly back to the gateway rather than
    bubble it as a generic device error.
    """


def resolve_envelope_labware(
    labware: LabwareDefinitionDTO,
    instance_name: str,
) -> Resource:
    """Materialize a PLR Resource from an envelope DTO.

    PLR-registered labware (`plr_class_name` set) resolves via the
    canonical PLR factory's by-name instantiation. Custom labware
    (no `plr_class_name`) resolves via geometry reconstruction through
    `PLRLabwareConverter`.

    `instance_name` is the runtime identity for the resource (typically
    the command's `command_id`, or a per-execution labware-instance id
    -- driver code chooses). Distinct from `labware.labware_type`, which
    names the catalog row.
    """
    if labware.plr_class_name is not None:
        return _resolve_from_plr_registry(labware.plr_class_name, instance_name)
    return _resolve_custom(labware, instance_name)


def _resolve_from_plr_registry(plr_class_name: str, instance_name: str) -> Resource:
    plr_factory = getattr(plr_resources, plr_class_name, None)
    if plr_factory is not None:
        if not callable(plr_factory):
            raise EnvelopeLabwareError(
                f"pylabrobot.resources.{plr_class_name} is not callable"
            )
        result = plr_factory(name=instance_name)
        if not isinstance(result, Resource):
            raise EnvelopeLabwareError(
                f"PLR factory {plr_class_name!r} returned {type(result).__name__}, "
                f"not a Resource subclass"
            )
        return result

    cd_factory = getattr(cd_plates, plr_class_name, None)
    if cd_factory is not None:
        if not callable(cd_factory):
            raise EnvelopeLabwareError(
                f"cheshire_drivers.plr.plates.{plr_class_name} is not callable"
            )
        adapter = cd_factory(name=instance_name)
        unwrapped = _unwrap_adapter(adapter)
        if not isinstance(unwrapped, Resource):
            raise EnvelopeLabwareError(
                f"cheshire_drivers.plr.plates wrapper {plr_class_name!r} "
                f"returned {type(adapter).__name__}; unwrap yielded "
                f"{type(unwrapped).__name__ if unwrapped is not None else 'None'}"
            )
        return unwrapped

    raise EnvelopeLabwareError(
        f"PLR factory {plr_class_name!r} not found in pylabrobot.resources "
        f"or cheshire_drivers.plr.plates"
    )


def _unwrap_adapter(adapter: object) -> Resource | None:
    """Extract the inner PLR Resource from a cheshire_drivers PLR*Adapter.

    Shares attribute names with `cheshire_drivers.scripts.regen_labware_seed._unwrap_adapter`
    but differs in no-match behavior: this returns `None` so the caller
    can raise loudly, where the regen helper returns the adapter
    unchanged so the seed walk can keep going.

    Attribute map: PLRPlateAdapter stores its Resource on `_plate`;
    PLRTipRackAdapter on `_rack`; PLRTroughAdapter on `_trough`. The
    adapters are same-package collaborators of the seed-gen, so the
    private-attr reach is acceptable for this build-time-style resolver.
    """
    for attr in ("_plate", "_rack", "_trough"):
        inner = getattr(adapter, attr, None)
        if inner is not None:
            return inner
    return None


def _resolve_custom(labware: LabwareDefinitionDTO, instance_name: str) -> Resource:
    if labware.category != "plate":
        # PLRLabwareConverter rebuilds plate geometry only, so a tip rack,
        # trough, tube or carrier has nothing to rebuild from. Refuse loudly
        # rather than return a half-built resource.
        raise EnvelopeLabwareError(
            f"custom labware reconstruction supports plate only (got "
            f"category={labware.category!r}, labware_type={labware.labware_type!r}). Set "
            f"plr_class_name to use PLR-registered resolution."
        )
    try:
        entry = PlateSeedEntry.model_validate(labware.geometry)
    except Exception as exc:
        raise EnvelopeLabwareError(
            f"envelope geometry failed PlateSeedEntry validation for "
            f"labware_type={labware.labware_type!r}: {exc}"
        ) from exc
    # The PlateSeedEntry exposes `.name` as the labware_type. For the materialized
    # PLR Plate we want the runtime instance name instead, so swap in a
    # frozen copy with `labware_type` set to the desired instance name; PlateSeedEntry
    # is a frozen Pydantic model so model_copy returns a fresh frozen instance.
    instance_entry = entry.model_copy(update={"labware_type": instance_name})
    return PLRLabwareConverter().to_plr_plate(instance_entry)
