"""An assignment runs beside the receive loop, one device's bad setup does not cost the others theirs, and every assignment is answered."""

import asyncio
from collections.abc import AsyncIterator
from pydantic import JsonValue

from cheshire_drivers.gateway_protocol import (
    AssignmentResult,
    CommandMessage,
    DeviceAssignment,
    MessageEnvelope,
    ResponseMessage,
)
from orca_client.client.websocket_client import WebSocketClient
from orca_client.config.models import ClientConfig, PlatformConfig
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.executor import ClientCommandExecutor


class _Socket:
    def __init__(self, inbound: list[str]) -> None:
        self.frames: list[str] = []
        self._inbound = inbound

    async def send(self, message: str) -> None:
        self.frames.append(message)

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        raise StopAsyncIteration


class _Executor(ClientCommandExecutor):
    def __init__(self) -> None:
        super().__init__(DeviceRegistry())
        self.ran: list[str] = []

    async def execute(self, cmd: CommandMessage) -> ResponseMessage:
        self.ran.append(cmd.command)
        return ResponseMessage(command_id=cmd.command_id, success=True, result=None)


class _Client(WebSocketClient):
    """Publishes no device status: these tests read only what the assignment sends back."""

    async def publish_device_status(self) -> None:
        return None


def _client(executor: _Executor, applied: list[DeviceAssignment], release: asyncio.Event, socket: _Socket) -> _Client:
    async def apply(assignment: DeviceAssignment) -> AssignmentResult:
        applied.append(assignment)
        await release.wait()
        return AssignmentResult(assignment_id=assignment.assignment_id, devices=[])

    config = ClientConfig(client_id="lab_pc", site="s", lab="l", platform=PlatformConfig(url="ws://localhost", api_key="k"))
    client = _Client(config, executor, apply)
    setattr(client, "ws", socket)
    return client


def _assignment(devices: list[JsonValue]) -> str:
    return MessageEnvelope(type="assignment", payload={"assignment_id": "a1", "devices": devices}).model_dump_json()


def _command() -> str:
    command = CommandMessage(command_id="c1", device_name="shaker_1", command="shake", params={}, effective_mode="LIVE")
    return MessageEnvelope.wrap_command(command).model_dump_json()


async def test_a_command_is_handled_while_an_assignment_waits_for_a_running_command() -> None:
    executor, applied, release = _Executor(), list[DeviceAssignment](), asyncio.Event()
    client = _client(executor, applied, release, _Socket([_assignment([{"name": "shaker_1", "kind": "shaker"}]), _command()]))

    async def until_the_command_ran() -> None:
        while not executor.ran:
            await asyncio.sleep(0)

    await asyncio.wait_for(client._receive_loop(), timeout=1.0)
    await asyncio.wait_for(until_the_command_ran(), timeout=1.0)

    assert executor.ran == ["shake"] and len(applied) == 1
    release.set()
    await asyncio.gather(*client._command_tasks)


async def test_a_device_whose_setup_cannot_be_read_is_reported_and_the_rest_are_built() -> None:
    executor, applied, release = _Executor(), list[DeviceAssignment](), asyncio.Event()
    release.set()
    socket = _Socket([_assignment([
        {"name": "shaker_1", "kind": "shaker"},
        {"name": "sealer_1", "kind": "sealer", "setup": {"a_field_from_a_newer_server": 1}},
    ])])
    client = _client(executor, applied, release, socket)

    await client._receive_loop()
    await asyncio.gather(*client._command_tasks)

    assert [d.name for d in applied[0].devices] == ["shaker_1"]
    result = MessageEnvelope.model_validate_json(socket.frames[-1]).unwrap()
    assert isinstance(result, AssignmentResult) and "a_field_from_a_newer_server" in result.errors["sealer_1"]


async def test_the_result_names_the_assignment_it_answers() -> None:
    executor, applied, release = _Executor(), list[DeviceAssignment](), asyncio.Event()
    release.set()
    socket = _Socket([_assignment([{"name": "shaker_1", "kind": "shaker"}])])
    client = _client(executor, applied, release, socket)

    await client._receive_loop()
    await asyncio.gather(*client._command_tasks)

    assert applied[0].assignment_id == "a1"
    result = MessageEnvelope.model_validate_json(socket.frames[-1]).unwrap()
    assert isinstance(result, AssignmentResult) and result.assignment_id == "a1"


async def test_an_assignment_that_fails_to_apply_is_answered_with_the_error_so_the_server_does_not_wait() -> None:
    async def fail(assignment: DeviceAssignment) -> AssignmentResult:
        raise RuntimeError("factory broke")

    socket = _Socket([_assignment([{"name": "shaker_1", "kind": "shaker"}])])
    config = ClientConfig(client_id="lab_pc", site="s", lab="l", platform=PlatformConfig(url="ws://localhost", api_key="k"))
    client = _Client(config, _Executor(), fail)
    setattr(client, "ws", socket)

    await client._receive_loop()
    await asyncio.gather(*client._command_tasks)

    result = MessageEnvelope.model_validate_json(socket.frames[-1]).unwrap()
    assert isinstance(result, AssignmentResult)
    assert result.assignment_id == "a1"
    assert result.error == "RuntimeError: factory broke"
