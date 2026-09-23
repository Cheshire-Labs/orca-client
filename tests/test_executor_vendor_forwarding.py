"""A forwarding driver's reason for refusing a command reaches the operator.

A driver that forwards to vendor objects advertises them from its class, before
the robot has answered, so the advertised set can name hardware that is not
fitted: an Opentrons Flex with no gripper on its extension mount still
advertises `gripper.ungrip`. Dispatching one of those raises an AttributeError
that says which hardware is absent. The executor used to probe with `hasattr`,
which discards that and answers "does not support command", sending an operator
to look for a typo in a name that was correct.
"""

import pytest

from cheshire_drivers.gateway_protocol import CommandMessage
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


class _FlexWithNoGripper:
    """Advertises the jaws, cannot reach them: nothing is fitted."""

    interfaces = frozenset({"IForceGripperJaw"})

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        raise AttributeError(
            f"FlexLiquidHandlerDriver cannot reach '_flex.gripper': 'gripper' "
            f"is not attached. The hardware it needs is absent or the device "
            f"has not connected yet."
        )


@pytest.fixture
def registry() -> DeviceRegistry:
    registry = DeviceRegistry()
    registry.register(
        name="flex_1", device_type="liquid_handler", driver=_FlexWithNoGripper(),
    )
    return registry


@pytest.mark.asyncio
async def test_absent_hardware_is_named_in_the_error(registry: DeviceRegistry) -> None:
    response = await CommandExecutor(registry).execute(
        CommandMessage(
            command_id="c1",
            device_name="flex_1",
            command="gripper.ungrip",
            params={},
            effective_mode="LIVE",
        )
    )

    assert response.success is False
    assert response.error_type == "UnsupportedCommandError"
    assert response.error is not None
    assert "gripper" in response.error
    assert "not attached" in response.error
