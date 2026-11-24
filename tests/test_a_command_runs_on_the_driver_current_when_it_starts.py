"""A command queued behind a device being replaced runs on the new driver, and one whose device was removed is refused."""

import asyncio

from cheshire_drivers.device_setup import DeviceSetup, SimDriverSetup
from cheshire_drivers.faults import RaiseFault
from cheshire_drivers.gateway_protocol import AssignedDevice, CommandMessage, DeviceAssignment, ResponseMessage

from orca_client.config.models import ClientConfig
from orca_client.session import ClientSession


def _shaker(setup: DeviceSetup) -> DeviceAssignment:
    return DeviceAssignment(assignment_id="a", devices=[AssignedDevice(kind="shaker", name="shaker_1", setup=setup)])


def _lock_plate() -> CommandMessage:
    return CommandMessage(command_id="c1", device_name="shaker_1", command="lock_plate", params={}, effective_mode="SIM")


async def _queue_behind(session: ClientSession, assignment: DeviceAssignment) -> ResponseMessage:
    """Hold the device, let the assignment queue on it, then a command, then let go."""
    lock = session.registry.lock_for("shaker_1")
    assert lock is not None
    await lock.acquire()
    applying = asyncio.create_task(session.apply_assignment(assignment))
    await asyncio.sleep(0)
    running = asyncio.create_task(session.executor.execute(_lock_plate()))
    for _ in range(5):
        await asyncio.sleep(0)
    lock.release()
    await applying
    return await asyncio.wait_for(running, timeout=5.0)


async def test_a_command_queued_behind_a_replace_runs_on_the_new_driver(client_config: ClientConfig) -> None:
    session = ClientSession(client_config)
    await session.apply_assignment(_shaker(DeviceSetup()))
    old = session.registry.get_sim_driver("shaker_1")
    faulted = DeviceSetup(sim=SimDriverSetup(faults=[RaiseFault(method="lock_plate", error_type="RuntimeError")]))

    response = await _queue_behind(session, _shaker(faulted))

    assert session.registry.get_sim_driver("shaker_1") is not old
    assert not response.success and response.error_type == "RuntimeError"


async def test_a_command_queued_behind_a_removal_is_refused_saying_the_device_was_removed(
    client_config: ClientConfig,
) -> None:
    session = ClientSession(client_config)
    await session.apply_assignment(_shaker(DeviceSetup()))

    response = await _queue_behind(session, DeviceAssignment(assignment_id="b", devices=[]))

    assert not response.success
    assert response.error_type == "DeviceRemovedError"
    assert "removed" in (response.error or "")
