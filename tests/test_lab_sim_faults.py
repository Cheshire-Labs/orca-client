"""Tests for fault wiring through DeviceConfig -> DeviceFactory -> Sim*Driver.

Covers:
  - Pre-armed FaultSpecs reach the constructed sim driver
  - DeviceConfig rejects faults on non-lab_sim drivers at parse time
  - Bad method names + bad error_type strings are rejected when the
    Sim*Driver is built (cheshire-drivers' own validation kicks in via
    the factory's Sim*Driver(... faults=...) call).
"""

import pytest

from cheshire_drivers import FaultSpec, HangFault, RaiseFault, SimShakerDriver
from cheshire_drivers.shaker_models import ShakeRequest

from orca_client.config.models import DeviceConfig, DriverConfig
from orca_client.devices.factory import DeviceFactory


def _lab_sim_config(faults: list[FaultSpec] | None = None) -> DeviceConfig:
    return DeviceConfig(
        type="shaker", name="s1",
        driver=DriverConfig(type="lab_sim", scale_factor=0.0),
        faults=faults or [],
    )


def test_faults_reach_constructed_driver() -> None:
    """The factory passes config.faults through to the Sim*Driver ctor."""
    cfg = _lab_sim_config(faults=[
        RaiseFault(method="shake", error_type="RuntimeError", on_calls=[1]),
    ])
    driver = DeviceFactory().create_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    # The fault registry on the driver should hold one raise spec for shake
    assert driver._faults._raise.get("shake")
    assert len(driver._faults._raise["shake"]) == 1


@pytest.mark.asyncio
async def test_constructed_driver_actually_faults() -> None:
    """End-to-end: driver built via factory raises on first call as armed."""
    cfg = _lab_sim_config(faults=[
        RaiseFault(method="shake", error_type="ValueError", message="armed", on_calls=[1]),
    ])
    driver = DeviceFactory().create_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    with pytest.raises(ValueError, match="armed"):
        await driver.shake(ShakeRequest(speed=500, duration=0.1))
    # call 2 succeeds (recoverable)
    await driver.shake(ShakeRequest(speed=500, duration=0.1))


def test_faults_on_plr_rejected_at_parse_time() -> None:
    """Mixing faults with a non-lab_sim driver is a config error, not silent."""
    with pytest.raises(ValueError, match="only wired through the ``lab_sim`` factory"):
        DeviceConfig(
            type="shaker", name="s1",
            driver=DriverConfig(type="plr", backend="InhecoThermoShake"),
            faults=[RaiseFault(method="shake", error_type="RuntimeError")],
        )


def test_faults_on_venus_rejected() -> None:
    with pytest.raises(ValueError, match="only wired through the ``lab_sim`` factory"):
        DeviceConfig(
            type="liquid_handler", name="lh1",
            driver=DriverConfig(type="venus"),
            faults=[RaiseFault(method="run_protocol", error_type="RuntimeError")],
        )


def test_faults_on_sim_rejected() -> None:
    """Plain `sim` is also rejected - only `lab_sim` honors faults today."""
    with pytest.raises(ValueError, match="only wired through the ``lab_sim`` factory"):
        DeviceConfig(
            type="shaker", name="s1",
            driver=DriverConfig(type="sim"),
            faults=[RaiseFault(method="shake", error_type="RuntimeError")],
        )


def test_factory_rejects_bad_method_via_cheshire_drivers() -> None:
    """A FaultSpec.method typo is caught when the Sim*Driver is constructed."""
    cfg = _lab_sim_config(faults=[
        RaiseFault(method="shak", error_type="RuntimeError"),
    ])
    with pytest.raises(ValueError, match="not a faultable method"):
        DeviceFactory().create_driver(cfg)


def test_factory_rejects_bad_error_type_via_cheshire_drivers() -> None:
    """An unresolvable error_type is caught at registry construction."""
    cfg = _lab_sim_config(faults=[
        RaiseFault(method="shake", error_type="NotARealError"),
    ])
    with pytest.raises(ValueError, match="does not resolve"):
        DeviceFactory().create_driver(cfg)


def test_hang_fault_also_wires_through() -> None:
    cfg = _lab_sim_config(faults=[
        HangFault(method="shake", extra_seconds=0.5, on_calls=[1]),
    ])
    driver = DeviceFactory().create_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    assert driver._faults._hang.get("shake")


def test_default_no_faults() -> None:
    """A DeviceConfig with no `faults` field constructs a clean driver."""
    cfg = _lab_sim_config()
    driver = DeviceFactory().create_driver(cfg)
    assert isinstance(driver, SimShakerDriver)
    assert not driver._faults.has_any()
