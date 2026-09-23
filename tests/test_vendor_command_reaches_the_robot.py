"""A vendor command sent over the wire reaches the hardware it names.

The other vendor-command tests each check one seam against a double of the
next: the driver resolves the name, the executor keeps the error, the facade
asks the gateway for the right string. None of them shows a command arriving.

This one starts where the gateway's WebSocket delivers a `CommandMessage` and ends
at the HTTP command the Flex's robot-server receives, with a real
FlexLiquidHandlerDriver and real PyLabRobot objects in between. Only the robot
is a double.
"""

import inspect

import pytest

from pydantic import JsonValue

from cheshire_drivers.gateway_protocol import CommandMessage, ResponseMessage
from cheshire_drivers.plr import FlexLiquidHandlerDriver
from pylabrobot.opentrons import ChatterboxTransport

from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


EIGHT_CHANNEL = ("p1000_multi_flex", 8, 5.0, 1000.0, "left")


async def _connected_flex() -> tuple[FlexLiquidHandlerDriver, ChatterboxTransport]:
    """A Flex that has come up with a gripper on its extension mount."""
    transport = ChatterboxTransport(pipettes=[EIGHT_CHANNEL], gripper=True)
    driver = FlexLiquidHandlerDriver(host="localhost", transport=transport)
    await driver.initialize()
    return driver, transport


async def _send(
    driver: FlexLiquidHandlerDriver,
    command: str,
    params: dict[str, JsonValue] | None = None,
) -> ResponseMessage:
    registry = DeviceRegistry()
    registry.register(name="flex_1", device_type="liquid_handler", driver=driver)
    return await CommandExecutor(registry).execute(
        CommandMessage(
            command_id=f"c-{command}",
            device_name="flex_1",
            command=command,
            params=params or {},
            effective_mode="LIVE",
        )
    )


def _sent(transport: ChatterboxTransport) -> list[str]:
    return [c["commandType"] for c in transport.commands]


@pytest.mark.asyncio
async def test_gripper_ungrip_reaches_the_robots_recovery_command() -> None:
    """The jaws holding a plate only open for unsafe/ungripLabware. Nothing
    reached it before: `release_jaw` sends robot/openGripperJaw, and no other
    driver verb touches the gripper object."""
    driver, transport = await _connected_flex()

    response = await _send(driver, "gripper.ungrip")

    assert response.success is True, response.error
    assert "unsafe/ungripLabware" in _sent(transport)


@pytest.mark.asyncio
async def test_a_vendor_command_carries_its_arguments_through() -> None:
    """A forwarded command takes plain kwargs off the wire; a name that arrived
    intact but lost its arguments would close the jaws at the wrong force."""
    driver, transport = await _connected_flex()

    response = await _send(driver, "gripper.grip", {"force": 12.0})

    assert response.success is True, response.error
    grips = [c for c in transport.commands if c["commandType"] == "robot/closeGripperJaw"]
    assert grips and grips[-1]["params"]["force"] == 12.0


@pytest.mark.asyncio
async def test_an_unprefixed_vendor_command_reaches_the_robot_itself() -> None:
    """The robot's own surface keeps bare names, and they must not be read as a
    prefix-less mistake and dropped."""
    driver, transport = await _connected_flex()

    response = await _send(driver, "retract_axis", {"axis": "leftZ"})

    assert response.success is True, response.error
    assert "retractAxis" in _sent(transport)


@pytest.mark.asyncio
async def test_a_vendor_commands_own_refusal_reaches_the_operator() -> None:
    """The vendor object validates its arguments and names what it accepts.
    That message is the whole value of dispatching by the real signature, so it
    must survive the trip back instead of becoming a generic failure."""
    driver, transport = await _connected_flex()

    response = await _send(driver, "retract_axis", {"axis": "z"})

    assert response.success is False
    assert response.error is not None and "leftZ" in response.error
    assert "retractAxis" not in _sent(transport)


@pytest.mark.asyncio
async def test_a_driver_verb_still_wins_over_the_vendor_method_underneath() -> None:
    """`home` is on the driver and on the robot, with different signatures: the
    driver's takes a HomeRequest, the robot's takes nothing. The executor
    resolves the name with getattr, so this pins which object that lands on.
    A workflow's home reaching the raw one would skip the driver's typed
    validation."""
    driver, _ = await _connected_flex()

    resolved = getattr(driver, "home")

    # The driver's own method, not the vendor's: the vendor's `home` takes no
    # request. Asserted by signature rather than by where it is defined, so
    # moving it to a shared base is not a failure.
    assert "request" in inspect.signature(resolved).parameters

    response = await _send(driver, "home", {})

    assert response.success is True, response.error


@pytest.mark.asyncio
async def test_what_the_handshake_advertises_is_what_dispatches() -> None:
    """The gate accepts exactly the advertised set, so a name that is
    advertised but not dispatchable is a command an operator is invited to
    send and then refused."""
    driver, _ = await _connected_flex()
    registry = DeviceRegistry()
    registry.register(name="flex_1", device_type="liquid_handler", driver=driver)

    advertised = registry.connect_info_list()[0].capabilities

    assert "gripper.ungrip" in advertised, advertised

    # Every advertised name whose vendor object is actually fitted, not three
    # hand-picked ones. Commands on an absent mount are advertised too, which
    # is a separate defect in `derive_capabilities`: it reads the declared
    # surface classes and cannot see what the instrument is carrying. This
    # fixture has one 8-channel and a gripper, so `head96.` and `right.` name
    # hardware that is not there.
    fitted = [
        command for command in advertised
        if not command.startswith(("head96.", "right."))
    ]
    unreachable = [
        command for command in fitted
        if not callable(getattr(driver, command, None))
    ]
    assert unreachable == [], (
        f"advertised and not dispatchable, so an operator is invited to send "
        f"them and then refused: {unreachable}"
    )
    assert len(fitted) > 10, f"the check narrowed to nothing: {fitted}"
