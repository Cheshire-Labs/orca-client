"""The outbound send path serializes frames.

Commands dispatch as concurrent tasks, so multiple _handle_command coroutines
(and the heartbeat) can call _send at the same time. websockets' send() is not
safe to interleave across coroutines, so _send holds a lock.
"""

from tests.no_assignment import build_nothing
import asyncio
import json

import pytest

from cheshire_drivers.gateway_protocol import AssignmentResult, DeviceAssignment, HeartbeatMessage, MessageEnvelope
from orca_client.client.websocket_client import WebSocketClient
from orca_client.config.models import ClientConfig
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.executor import ClientCommandExecutor


class _OverlapDetectingWs:
    """Records whether two send() calls were ever in flight at once."""

    def __init__(self) -> None:
        self.saw_overlap = False
        self._active = 0

    async def send(self, message: str) -> None:
        self._active += 1
        if self._active > 1:
            self.saw_overlap = True
        # Yield so an unserialized concurrent send interleaves here and is seen.
        await asyncio.sleep(0)
        self._active -= 1


@pytest.mark.asyncio
async def test_concurrent_send_is_serialized(
    client_config: ClientConfig,
    device_registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = WebSocketClient(client_config, ClientCommandExecutor(device_registry), apply_assignment=build_nothing)
    fake_ws = _OverlapDetectingWs()
    monkeypatch.setattr(client, "ws", fake_ws)

    envelope = MessageEnvelope.wrap_heartbeat(
        HeartbeatMessage(timestamp=0.0, uptime=0.0)
    )
    # Without the send lock, these 10 sends interleave and overlap; with it they
    # run one at a time.
    await asyncio.gather(*[client._send(envelope) for _ in range(10)])

    assert not fake_ws.saw_overlap


class _CapturingWs:
    """Records every frame sent."""

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send(self, message: str) -> None:
        self.frames.append(message)


@pytest.mark.asyncio
async def test_assignment_result_serializes_frozenset_capabilities(
    client_config: ClientConfig,
    device_registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An assignment result advertises each device's interfaces/capabilities as
    frozensets, but the envelope payload is typed dict[str, JsonValue], which
    rejects frozenset. The result must be JSON-normalized (frozenset -> list)
    before building the envelope.
    """
    async def report_held_devices(assignment: DeviceAssignment) -> AssignmentResult:
        return AssignmentResult(assignment_id=assignment.assignment_id, devices=device_registry.connect_info_list())

    client = WebSocketClient(client_config, ClientCommandExecutor(device_registry), apply_assignment=report_held_devices)
    fake_ws = _CapturingWs()
    monkeypatch.setattr(client, "ws", fake_ws)

    # Must not raise on the frozenset interfaces/capabilities.
    await client._handle_assignment(MessageEnvelope.wrap_assignment(DeviceAssignment(assignment_id="a1", devices=[])))

    assert len(fake_ws.frames) == 1
    devices = json.loads(fake_ws.frames[0])["payload"]["devices"]
    assert devices, "the assignment result must advertise the registered devices"
    assert isinstance(devices[0]["interfaces"], list)
    assert isinstance(devices[0]["capabilities"], list)
