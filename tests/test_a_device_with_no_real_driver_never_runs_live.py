"""A device with no real driver, or set always_sim, gets no LIVE driver; a LIVE command to it is refused."""

import pytest

from cheshire_drivers.device_setup import DeviceSetup, RealDriverSetup
from cheshire_drivers.gateway_protocol import AssignedDevice, CommandMessage
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.devices import DeviceFactory
from orca_client.executor import ClientCommandExecutor


def test_a_device_with_no_real_driver_has_no_live_driver(
    device_factory: DeviceFactory, sim_shaker_config: AssignedDevice,
) -> None:
    assert sim_shaker_config.setup.real is None
    assert device_factory.create_driver(sim_shaker_config) is None


def test_an_always_sim_device_with_a_real_driver_has_no_live_driver(device_factory: DeviceFactory) -> None:
    setup = DeviceSetup(client="lab_pc", real=RealDriverSetup(type="human"), always_sim=True)
    arm = AssignedDevice(kind="transporter", name="arm", setup=setup)

    assert device_factory.create_driver(arm) is None
    assert device_factory.create_driver(arm.model_copy(update={"setup": setup.model_copy(update={"always_sim": False})})) is not None


@pytest.mark.asyncio
async def test_a_live_command_to_it_is_refused_and_a_sim_command_runs(
    device_factory: DeviceFactory, sim_shaker_config: AssignedDevice,
) -> None:
    registry = DeviceRegistry()
    registry.register(
        name=sim_shaker_config.name,
        device_type=sim_shaker_config.kind,
        driver=device_factory.create_driver(sim_shaker_config),
        sim_driver=device_factory.create_sim_driver(sim_shaker_config),
    )
    try:
        executor = ClientCommandExecutor(registry)

        def command(mode: str) -> CommandMessage:
            return CommandMessage(
                command_id=f"cmd_{mode}", device_name=sim_shaker_config.name, command="shake",
                params={"speed": 300.0, "duration": 1.0}, effective_mode=mode,
            )

        live = await executor.execute(command("LIVE"))
        sim = await executor.execute(command("SIM"))
    finally:
        await registry.cleanup_all()

    assert live.success is False
    assert live.error_type == "DeviceNotFoundError"
    assert sim.success is True
