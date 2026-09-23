"""Tests for the `lab_sim` driver type variant.

A device configured with `driver.type=lab_sim` gets a cheshire-drivers
`Sim*Driver` paired with a `SleepSim(scale_factor=...)` strategy. A shake,
seal or centrifuge `duration` then adds `duration * scale_factor` seconds to
the sim's settle time, exercising the LIVE wire path end to end without real
hardware.

Default `scale_factor=0.0` preserves the historical instant-settle
behavior so unit-test runs that didn't bump the factor stay fast.
"""

import pytest

from cheshire_drivers import (
    SimShakerDriver,
    SimCentrifugeDriver,
    SimSealerDriver,
    SimTransporterDriver,
    SimLiquidHandlerDriver,
    SimPlateWasherDriver,
    SimReaderDriver,
    SimDelidderDriver,
    SimStorageDriver,
    SimTranslatorDriver,
    SimWasteDriver,
)
from cheshire_drivers.shaker_models import ShakeRequest
from cheshire_drivers.sims import SleepSim

from orca_client.config.models import (
    ConnectionConfig,
    DeviceConfig,
    DriverConfig,
)
from orca_client.devices.factory import DeviceFactory


def _config(device_type: str, scale_factor: float = 0.0) -> DeviceConfig:
    return DeviceConfig(
        device_id=f"{device_type}_1",
        type=device_type,  # type: ignore[arg-type]
        name=device_type,
        driver=DriverConfig(type="lab_sim", scale_factor=scale_factor),
    )


class TestLabSimDriverType:
    """`lab_sim` is a closed Literal alongside `plr`, `venus`, `sim`."""

    def test_shaker_returns_sim_shaker_driver(self) -> None:
        factory = DeviceFactory()
        driver = factory.create_driver(_config("shaker"))
        assert isinstance(driver, SimShakerDriver)

    def test_centrifuge_returns_sim_centrifuge_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("centrifuge"))
        assert isinstance(driver, SimCentrifugeDriver)

    def test_sealer_returns_sim_sealer_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("sealer"))
        assert isinstance(driver, SimSealerDriver)

    def test_transporter_returns_sim_transporter_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("transporter"))
        assert isinstance(driver, SimTransporterDriver)

    def test_translator_returns_sim_translator_driver(self) -> None:
        """A bridge must get the one-carriage driver, not the generic arm:
        its `place` guard is what refuses a second plate onto the carriage."""
        driver = DeviceFactory().create_driver(_config("translator"))
        assert isinstance(driver, SimTranslatorDriver)
        assert driver.single_carriage is True

    def test_liquid_handler_returns_sim_lh_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("liquid_handler"))
        assert isinstance(driver, SimLiquidHandlerDriver)

    def test_plate_washer_returns_sim_plate_washer_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("plate_washer"))
        assert isinstance(driver, SimPlateWasherDriver)

    def test_reader_returns_sim_reader_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("reader"))
        assert isinstance(driver, SimReaderDriver)

    def test_delidder_returns_sim_delidder_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("delidder"))
        assert isinstance(driver, SimDelidderDriver)

    def test_storage_uses_sim_storage_driver(self) -> None:
        """`storage` device type under `lab_sim` builds a SimStorageDriver
        with the configured scale-factor SleepSim strategy and the
        device's fault list (here defaulted empty)."""
        driver = DeviceFactory().create_driver(_config("storage", scale_factor=0.5))
        assert isinstance(driver, SimStorageDriver)
        assert isinstance(driver._sim_strategy, SleepSim)
        assert driver._sim_strategy.scale_factor == 0.5

    def test_waste_uses_sim_waste_driver(self) -> None:
        """`waste` device type under `lab_sim` builds a SimWasteDriver
        with the configured scale-factor SleepSim strategy."""
        driver = DeviceFactory().create_driver(_config("waste", scale_factor=0.5))
        assert isinstance(driver, SimWasteDriver)
        assert isinstance(driver._sim_strategy, SleepSim)
        assert driver._sim_strategy.scale_factor == 0.5


class TestLabSimScaleFactor:
    """`scale_factor` from config flows into the driver's `SleepSim` strategy."""

    def test_default_scale_factor_is_zero(self) -> None:
        cfg = DriverConfig(type="lab_sim")
        assert cfg.scale_factor == 0.0

    def test_scale_factor_strategy_attached(self) -> None:
        driver = DeviceFactory().create_driver(_config("shaker", scale_factor=0.5))
        assert isinstance(driver._sim_strategy, SleepSim)
        assert driver._sim_strategy.scale_factor == 0.5

    @pytest.mark.asyncio
    async def test_default_zero_scale_factor_keeps_shake_fast(
        self, recorded_sleeps: list[float]
    ) -> None:
        """scale_factor=0.0 ignores duration: a 60s shake waits only sim_time."""
        driver = DeviceFactory().create_driver(_config("shaker", scale_factor=0.0))
        assert isinstance(driver, SimShakerDriver)
        await driver.shake(ShakeRequest(speed=500, duration=60.0))
        assert recorded_sleeps == [pytest.approx(0.2)]

    @pytest.mark.asyncio
    async def test_positive_scale_factor_honors_duration(
        self, recorded_sleeps: list[float]
    ) -> None:
        """scale_factor=0.02 scales a 30s shake: 0.2s sim_time + 30*0.02 = 0.8s."""
        driver = DeviceFactory().create_driver(_config("shaker", scale_factor=0.02))
        assert isinstance(driver, SimShakerDriver)
        await driver.shake(ShakeRequest(speed=500, duration=30.0))
        assert recorded_sleeps == [pytest.approx(0.8)]
