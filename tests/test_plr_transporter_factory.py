"""Guard for the `plr` + `transporter` device-build path.

The arm is built from its plain-class driver rather than by class lookup,
because its gripper geometry has no defaults a bare `backend_class(host, port)`
could supply. These tests pin that path: the right class, the arm's own default
port, the setup's gripper values reaching the driver, and the retired backend
name failing loudly instead of building something else.
"""

from collections.abc import Mapping
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from pydantic import JsonValue

from cheshire_drivers.device_setup import DeviceSetup, RealDriverSetup, TcpConnection
from cheshire_drivers.gateway_protocol import AssignedDevice
from cheshire_drivers.plr import PLRTransporterBackendWrapper, PreciseFlexTransporterDriver

from orca_client.devices.factory import DeviceFactory


def _arm_config(
    arm: dict[str, JsonValue] | None = None,
    tcp_port: int | None = None,
    backend: str = "PreciseFlex",
) -> AssignedDevice:
    return AssignedDevice(
        kind="transporter",
        name="arm",
        setup=DeviceSetup(
            client="lab_pc",
            arm=arm,
            real=RealDriverSetup(
                type="plr",
                backend=backend,
                connection=TcpConnection(type="tcp", host="169.254.5.136", port=tcp_port),
            ),
        ),
    )


def test_the_arm_is_built_from_its_plain_class_driver() -> None:
    driver = DeviceFactory().create_driver(_arm_config())

    assert isinstance(driver, PreciseFlexTransporterDriver)
    assert isinstance(driver, PLRTransporterBackendWrapper)


def _built_with(config: AssignedDevice) -> Mapping[str, JsonValue]:
    """The keyword arguments the factory hands the arm driver."""
    with patch(
        "cheshire_drivers.plr.transporter.PreciseFlexTransporterDriver"
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
    built = _built_with(_arm_config({"gripper_z_offset": 4.5, "closed_gripper_position": 71.0}))

    assert built["gripper_z_offset"] == 4.5
    assert built["closed_gripper_position"] == 71.0


def test_a_setup_that_says_how_the_device_is_drawn_is_refused() -> None:
    """How a device is drawn is not the framework's: a setup naming a model, geometry or fitted parts does not load."""
    for field in ("model", "geometry", "fitted"):
        with pytest.raises(ValidationError, match=field):
            DeviceSetup.model_validate({field: "pf400" if field == "model" else {}})


def test_an_arm_with_no_build_block_gets_the_standard_reach_defaults() -> None:
    built = _built_with(_arm_config())

    assert built["gripper_z_offset"] == 0.0
    assert built["closed_gripper_position"] == 75.5
    assert built["has_rail"] is False


def test_the_retired_backend_name_is_refused() -> None:
    """`PreciseFlexBackend` is the legacy arm driver. A bench config still naming
    it must fail at build rather than quietly running the old one."""
    with pytest.raises(ImportError, match="PreciseFlexBackend"):
        DeviceFactory().create_driver(_arm_config(backend="PreciseFlexBackend"))


def test_a_backend_outside_the_map_still_takes_the_bare_wrapper_path() -> None:
    """Unmapped transporter backends are not broken by the arm's own entry."""
    with patch("cheshire_drivers.driver_lookup.get_plr_backend_class"):
        driver = DeviceFactory().create_driver(_arm_config(backend="SomeOtherArm"))

    assert isinstance(driver, PLRTransporterBackendWrapper)
    assert not isinstance(driver, PreciseFlexTransporterDriver)
