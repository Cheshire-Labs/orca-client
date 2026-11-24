"""A storage or waste device gets a simulator, so a SIM command to it has a driver."""

import pytest
from cheshire_drivers import SimStorageDriver, SimWasteDriver
from cheshire_drivers.command_executor import DeviceRegistry
from cheshire_drivers.gateway_protocol import AssignedDevice

from orca_client.devices import DeviceFactory


@pytest.mark.asyncio
async def test_register_storage_pairs_sim_driver(device_factory: DeviceFactory):
    config = AssignedDevice(kind="storage", name="test_storage")
    registry = DeviceRegistry()
    registry.register(
        name=config.name,
        device_type=config.kind,
        driver=device_factory.create_driver(config),
        sim_driver=device_factory.create_sim_driver(config),
    )

    sim_driver = registry.get_sim_driver("test_storage")
    assert sim_driver is not None
    assert isinstance(sim_driver, SimStorageDriver)


@pytest.mark.asyncio
async def test_register_waste_pairs_sim_driver(device_factory: DeviceFactory):
    config = AssignedDevice(kind="waste", name="test_waste")
    registry = DeviceRegistry()
    registry.register(
        name=config.name,
        device_type=config.kind,
        driver=device_factory.create_driver(config),
        sim_driver=device_factory.create_sim_driver(config),
    )

    sim_driver = registry.get_sim_driver("test_waste")
    assert sim_driver is not None
    assert isinstance(sim_driver, SimWasteDriver)
