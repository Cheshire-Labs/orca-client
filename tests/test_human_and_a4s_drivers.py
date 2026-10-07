"""A person moving plates and an A4S sealer are built by orca-client, like every device."""

from typing import Literal

import pytest
from pylabrobot.sealing import A4SBackend

from cheshire_drivers.command_timings import collect_command_timings
from cheshire_drivers.human_transporter_driver import HumanTransporterDriver
from cheshire_drivers.plr import A4SSealerDriver
from cheshire_drivers.move_parameters import SEED_MOVE_PARAMETERS
from cheshire_drivers.transporter_models import PickAtCoordsRequest
from cheshire_drivers.teachpoints import Teachpoint
from cheshire_drivers import CartesianCoordinates
from orca_client.config.models import ConnectionConfig, DeviceConfig, DriverConfig
from orca_client.devices import DeviceFactory


DeviceType = Literal["shaker", "sealer", "liquid_handler", "translator"]


def _human() -> DeviceConfig:
    return DeviceConfig(type="transporter", name="human_transfer", driver=DriverConfig(type="human"))


def test_a_human_transporter_is_prompted_in_live() -> None:
    assert isinstance(DeviceFactory().create_driver(_human()), HumanTransporterDriver)


def test_a_human_transporter_is_not_prompted_in_device_sim() -> None:
    """None means the registry pairs the plain simulated transporter, which does not prompt."""
    assert DeviceFactory().create_sim_driver(_human()) is None


async def test_the_live_human_driver_names_the_site_in_its_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    driver = DeviceFactory().create_driver(_human())
    assert isinstance(driver, HumanTransporterDriver)
    teachpoint = Teachpoint(
        "ml_star_position_1/sample_site", CartesianCoordinates(0, 0, 0, 0, 90, 180),
        orientation="right",
    )
    await driver.pick_at_coords(PickAtCoordsRequest(labware_type="plate_96", teachpoint=teachpoint, handling=SEED_MOVE_PARAMETERS))
    assert prompts and "PICK UP plate_96 from 'ml_star_position_1/sample_site'" in prompts[0]


@pytest.mark.parametrize("device_type", ["shaker", "sealer", "liquid_handler", "translator"])
def test_a_human_driver_is_refused_on_anything_but_a_transporter(device_type: DeviceType) -> None:
    with pytest.raises(ValueError, match="human"):
        DeviceConfig(type=device_type, name="x", driver=DriverConfig(type="human"))


def test_an_a4s_gets_the_driver_that_waits_out_a_seal_cycle() -> None:
    config = DeviceConfig(
        type="sealer", name="sealer_1",
        driver=DriverConfig(
            type="plr", backend="A4SBackend",
            connection=ConnectionConfig(type="serial", port="COM3"),
        ),
    )
    driver = DeviceFactory().create_driver(config)
    assert isinstance(driver, A4SSealerDriver)
    assert isinstance(driver._backend, A4SBackend)
    assert driver._backend.timeout >= collect_command_timings(A4SSealerDriver)["seal"].max_seconds

