"""A driver failure reaches the gateway with everything the driver knew.

The bench case: a Flex gripper move stalled and the robot named the axis in the
exception's payload. orca-client sent ``str(exc)``, so the gateway, the event
log and the operator all got "Stall or Collision Detected" and nothing else.
"""

import json

import pytest
from pylabrobot.opentrons.robot import OpentronsCommandError

from cheshire_drivers import SimShakerDriver
from cheshire_drivers.gateway_protocol import CommandMessage, MessageEnvelope
from orca_client.client.websocket_client import WebSocketClient
from orca_client.config.models import ClientConfig
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


def _stall() -> OpentronsCommandError:
    return OpentronsCommandError(
        "moveLabware",
        {
            "errorType": "StallOrCollisionDetectedError",
            "errorCode": "3008",
            "detail": "Stall or Collision Detected",
            "wrappedErrors": [
                {"errorType": "MotionFailedError", "detail": "Motor stall on gantry_y"}
            ],
        },
    )


def _shake_that_stalls(registry: DeviceRegistry) -> None:
    driver = registry.get_driver("test_shaker")
    assert driver is not None

    async def stall(*_args: float, **_kwargs: float) -> None:
        raise _stall()

    setattr(driver, "shake", stall)


def _shake_command() -> CommandMessage:
    return CommandMessage(
        command_id="cmd_stall",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )


@pytest.mark.asyncio
async def test_the_axis_that_stalled_reaches_the_wire(
    device_registry: DeviceRegistry,
) -> None:
    _shake_that_stalls(device_registry)

    response = await CommandExecutor(device_registry).execute(_shake_command())

    assert response.success is False
    assert response.error is not None
    assert "Motor stall on gantry_y" in response.error
    assert "StallOrCollisionDetectedError" in response.error


@pytest.mark.asyncio
async def test_the_error_type_still_names_the_exception_class(
    device_registry: DeviceRegistry,
) -> None:
    """The folded detail is extra text, not a change to what error_type means."""
    _shake_that_stalls(device_registry)

    response = await CommandExecutor(device_registry).execute(_shake_command())

    assert response.error_type == "OpentronsCommandError"


@pytest.mark.asyncio
async def test_an_ordinary_failure_still_reads_as_its_own_message(
    device_registry: DeviceRegistry,
) -> None:
    driver = device_registry.get_driver("test_shaker")
    assert driver is not None

    async def refuse(*_args: float, **_kwargs: float) -> None:
        raise RuntimeError("shaker lid is open")

    setattr(driver, "shake", refuse)

    response = await CommandExecutor(device_registry).execute(_shake_command())

    assert response.error == "shaker lid is open"


@pytest.mark.asyncio
async def test_a_property_that_raises_still_carries_the_detail(
    client_config: ClientConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one path where the websocket handler does the folding itself.

    `getattr` runs a property's getter outside the executor's try-block, so
    anything it raises other than AttributeError escapes `_execute_uncached`
    entirely and lands in `_handle_command`. That is where this line is the
    only thing standing between the driver's payload and the operator.
    """
    class _ShakerWithABadSensor(SimShakerDriver):
        @property
        def is_initialized(self) -> bool:
            raise _stall()

    registry = DeviceRegistry()
    registry.register(
        name="test_shaker",
        device_type="shaker",
        driver=_ShakerWithABadSensor("test_shaker"),
    )
    client = WebSocketClient(client_config, CommandExecutor(registry))
    sent: list[str] = []

    class _Ws:
        async def send(self, message: str) -> None:
            sent.append(message)

    monkeypatch.setattr(client, "ws", _Ws())
    monkeypatch.setattr(client, "_connected", True)

    await client._handle_command(MessageEnvelope.wrap_command(CommandMessage(
        command_id="cmd_prop",
        device_name="test_shaker",
        command="is_initialized",
        params={},
        effective_mode="LIVE",
    )))

    errors = [
        json.loads(f) for f in sent if json.loads(f).get("type") == "response"
    ]
    assert errors, f"nothing came back; frames were {sent}"
    text = errors[-1]["payload"]["error"]
    assert "Motor stall on gantry_y" in text, (
        f"the driver's payload was flattened to its first sentence: {text}"
    )
    assert "StallOrCollisionDetectedError" in text
