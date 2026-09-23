"""Guard for the `plr` + `transporter` device-build path.

This path had no coverage, so when cheshire-drivers' Path-2 refactor removed the
driver-side `bind_teachpoint_store` hook, the stale call left in
`DeviceFactory._create_plr_driver` went unnoticed -- building a real arm
transporter raised AttributeError at device-creation time.

The arm is now built from its plain-class driver rather than by class lookup,
because its gripper geometry has no defaults a bare `backend_class(host, port)`
could supply. These tests pin that path: the right class, the arm's own default
port, the config's gripper values reaching the driver, and the retired backend
name failing loudly instead of building something else.
"""

from typing import Any, Mapping
from unittest.mock import patch

import pytest
from cheshire_drivers.plr import PLRTransporterBackendWrapper, PreciseFlexTransporterDriver

from orca_client.config.models import ArmConfig, ConnectionConfig, DeviceConfig, DriverConfig
from orca_client.devices.factory import DeviceFactory


def _arm_config(arm: ArmConfig | None = None, tcp_port: int | None = None) -> DeviceConfig:
    return DeviceConfig(
        type="transporter",
        name="arm",
        driver=DriverConfig(
            type="plr",
            backend="PreciseFlex",
            connection=ConnectionConfig(type="tcp", host="169.254.5.136", tcp_port=tcp_port),
            arm=arm,
        ),
    )


def test_the_arm_is_built_from_its_plain_class_driver() -> None:
    driver = DeviceFactory().create_driver(_arm_config())

    assert isinstance(driver, PreciseFlexTransporterDriver)
    assert isinstance(driver, PLRTransporterBackendWrapper)


def _built_with(config: DeviceConfig) -> Mapping[str, Any]:
    """The keyword arguments the factory hands the arm driver."""
    with patch(
        "orca_client.devices.factory.PreciseFlexTransporterDriver"
    ) as driver_cls:
        DeviceFactory().create_driver(config)
    return driver_cls.call_args.kwargs


def test_an_arm_without_a_port_gets_the_controllers_own_default() -> None:
    """The shared tcp helper defaults to Opentrons' 31950, which would point the
    arm's socket at nothing."""
    assert _built_with(_arm_config())["port"] == 10100
    assert _built_with(_arm_config(tcp_port=10200))["port"] == 10200


def test_the_configured_gripper_reaches_the_driver() -> None:
    """The controller cannot report either of these, so a wrong value here is a
    wrong grip height and a gripper that never closes on the plate."""
    built = _built_with(_arm_config(ArmConfig(gripper_z_offset=4.5, closed_gripper_position=71.0)))

    assert built["gripper_z_offset"] == 4.5
    assert built["closed_gripper_position"] == 71.0


def test_an_arm_with_no_build_block_gets_the_standard_reach_defaults() -> None:
    built = _built_with(_arm_config())

    assert built["gripper_z_offset"] == 0.0
    assert built["closed_gripper_position"] == 75.5
    assert built["has_rail"] is False


def test_the_retired_backend_name_is_refused() -> None:
    """`PreciseFlexBackend` is the legacy arm driver. A bench config still naming
    it must fail at build rather than quietly running the old one."""
    config = _arm_config()
    config.driver.backend = "PreciseFlexBackend"

    with pytest.raises(ImportError, match="PreciseFlexBackend"):
        DeviceFactory().create_driver(config)


def test_a_backend_outside_the_map_still_takes_the_bare_wrapper_path() -> None:
    """Unmapped transporter backends are not broken by the arm's own entry."""
    config = _arm_config()
    config.driver.backend = "SomeOtherArm"
    with patch.object(DeviceFactory, "_create_plr_backend", return_value=object()):
        driver = DeviceFactory().create_driver(config)

    assert isinstance(driver, PLRTransporterBackendWrapper)
    assert not isinstance(driver, PreciseFlexTransporterDriver)
