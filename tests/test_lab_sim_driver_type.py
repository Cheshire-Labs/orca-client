"""Tests for the `lab_sim` driver type variant.

A device configured with `driver.type=lab_sim` gets a cheshire-drivers
`Sim*Driver` that takes pre-armed faults, exercising the LIVE wire path end to
end without real hardware. How long a step lasts is orca's sim time scale, not
a driver setting.
"""

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
from orca_client.config.models import DeviceConfig
from orca_client.devices.factory import DeviceFactory


def _config(device_type: str) -> DeviceConfig:
    return DeviceConfig.model_validate(
        {"type": device_type, "name": device_type, "driver": {"type": "lab_sim"}},
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
        driver = DeviceFactory().create_driver(_config("storage"))
        assert isinstance(driver, SimStorageDriver)

    def test_waste_uses_sim_waste_driver(self) -> None:
        driver = DeviceFactory().create_driver(_config("waste"))
        assert isinstance(driver, SimWasteDriver)
