"""Stopping the client must release the arm, not just the BaseDriver devices.

A transporter has no BaseDriver lifecycle, so shutdown used to skip it entirely.
On an arm that holds high power between commands, that left the motors energised
after the process exited: its `disconnect` is what drops them.
"""

import pytest
from cheshire_drivers.sims import SimTransporterDriver
from orca_client.devices.registry import DeviceRegistry

pytestmark = pytest.mark.asyncio


def _arm(name: str) -> SimTransporterDriver:
    """A real transporter driver, recording the lifecycle calls the registry makes."""
    arm = SimTransporterDriver(name)
    arm.calls = []
    original = arm.disconnect

    async def _spy() -> None:
        arm.calls.append("disconnect")
        await original()

    arm.disconnect = _spy
    return arm


def _arm_with_a_dead_link(name: str) -> SimTransporterDriver:
    arm = SimTransporterDriver(name)
    arm.calls = []

    async def _refuse() -> None:
        arm.calls.append("disconnect")
        raise RuntimeError("the link had already died")

    arm.disconnect = _refuse
    return arm


async def test_stopping_the_client_disconnects_the_arm() -> None:
    registry = DeviceRegistry()
    arm = _arm("arm_1")
    registry.register(name="arm_1", device_type="transporter", driver=arm)

    await registry.cleanup_all()

    assert arm.calls == ["disconnect"], "the client exited without releasing the arm"


async def test_an_arm_that_refuses_to_disconnect_does_not_strand_the_rest() -> None:
    """A dead link on one arm must not stop the next device being released."""
    registry = DeviceRegistry()
    first, second = _arm_with_a_dead_link("arm_1"), _arm("arm_2")
    registry.register(name="arm_1", device_type="transporter", driver=first)
    registry.register(name="arm_2", device_type="transporter", driver=second)

    await registry.cleanup_all()

    assert second.calls == ["disconnect"]
