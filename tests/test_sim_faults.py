"""Tests for fault wiring through DeviceSetup -> DeviceFactory -> Sim*Driver.

Covers:
  - Pre-armed FaultSpecs reach the constructed sim driver
  - Bad method names + bad error_type strings are rejected when the
    Sim*Driver is built (cheshire-drivers' own validation kicks in via
    the factory's Sim*Driver(... faults=...) call).
"""

import pytest

from cheshire_drivers import FaultSpec, HangFault, RaiseFault, SimShakerDriver
from cheshire_drivers.shaker_models import ShakeRequest

from cheshire_drivers.device_setup import DeviceSetup, SimDriverSetup
from cheshire_drivers.gateway_protocol import AssignedDevice
from orca_client.devices.factory import DeviceFactory


def _faulted_sim_config(faults: list[FaultSpec] | None = None) -> AssignedDevice:
    return AssignedDevice(kind="shaker", name="s1", setup=DeviceSetup(sim=SimDriverSetup(faults=faults or [])))


def test_faults_reach_constructed_driver() -> None:
    """The factory passes the sim's faults through to the Sim*Driver ctor."""
    cfg = _faulted_sim_config(faults=[
        RaiseFault(method="shake", error_type="RuntimeError", on_calls=[1]),
    ])
    driver = DeviceFactory().create_sim_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    # The fault registry on the driver should hold one raise spec for shake
    assert driver._faults._raise.get("shake")
    assert len(driver._faults._raise["shake"]) == 1


@pytest.mark.asyncio
async def test_constructed_driver_actually_faults() -> None:
    """End-to-end: driver built via factory raises on first call as armed."""
    cfg = _faulted_sim_config(faults=[
        RaiseFault(method="shake", error_type="ValueError", message="armed", on_calls=[1]),
    ])
    driver = DeviceFactory().create_sim_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    with pytest.raises(ValueError, match="armed"):
        await driver.shake(ShakeRequest(speed=500, duration=0.1))
    # call 2 succeeds (recoverable)
    await driver.shake(ShakeRequest(speed=500, duration=0.1))


def test_factory_rejects_bad_method_via_cheshire_drivers() -> None:
    """A FaultSpec.method typo is caught when the Sim*Driver is constructed."""
    cfg = _faulted_sim_config(faults=[
        RaiseFault(method="shak", error_type="RuntimeError"),
    ])
    with pytest.raises(ValueError, match="not a faultable method"):
        DeviceFactory().create_sim_driver(cfg)


def test_factory_rejects_bad_error_type_via_cheshire_drivers() -> None:
    """An unresolvable error_type is caught at registry construction."""
    cfg = _faulted_sim_config(faults=[
        RaiseFault(method="shake", error_type="NotARealError"),
    ])
    with pytest.raises(ValueError, match="does not resolve"):
        DeviceFactory().create_sim_driver(cfg)


def test_hang_fault_also_wires_through() -> None:
    cfg = _faulted_sim_config(faults=[
        HangFault(method="shake", extra_seconds=0.5, on_calls=[1]),
    ])
    driver = DeviceFactory().create_sim_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    assert driver._faults._hang.get("shake")


def test_default_no_faults() -> None:
    """A setup with no faults constructs a clean driver."""
    cfg = _faulted_sim_config()
    driver = DeviceFactory().create_sim_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    assert not driver._faults.has_any()
