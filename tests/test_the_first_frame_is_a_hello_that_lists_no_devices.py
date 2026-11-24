"""The first frame a client sends after it connects is a hello that names the client and lists no devices."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
import websockets

from cheshire_drivers.gateway_protocol import PROTOCOL_VERSION, ClientHello, DeviceAssignment, MessageEnvelope
from orca_client.client import WebSocketClient
from orca_client.config.models import ClientConfig
from cheshire_drivers.command_executor import DeviceRegistry
from orca_client.executor import ClientCommandExecutor
from tests.no_assignment import build_nothing


class _SocketThatStaysOpen:
    """A platform that keeps every frame the client sends, delivers its own frames, and stays open until closed."""

    def __init__(self, inbound: list[str]) -> None:
        self.frames: list[str] = []
        self._inbound = inbound
        self._closed = asyncio.Event()

    async def send(self, message: str) -> None:
        self.frames.append(message)

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._inbound:
            return self._inbound.pop(0)
        await self._closed.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self._closed.set()


def _dial(ws: _SocketThatStaysOpen) -> Callable[..., AbstractAsyncContextManager[_SocketThatStaysOpen]]:
    """Stands in for websockets.connect and hands out one socket."""

    @asynccontextmanager
    async def connect(
        uri: str, *, ssl: bool | None = None, additional_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[_SocketThatStaysOpen]:
        yield ws

    return connect


def _types(ws: _SocketThatStaysOpen) -> list[str]:
    return [MessageEnvelope.model_validate_json(frame).type for frame in ws.frames]


async def test_the_first_frame_is_a_hello_that_names_the_client_and_lists_no_devices(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = WebSocketClient(
        config=client_config, executor=ClientCommandExecutor(DeviceRegistry()), apply_assignment=build_nothing,
    )
    assignment = DeviceAssignment(assignment_id="a1", devices=[])
    ws = _SocketThatStaysOpen(inbound=[MessageEnvelope.wrap_assignment(assignment).model_dump_json()])
    monkeypatch.setattr(websockets, "connect", _dial(ws))

    running = asyncio.create_task(client.start())
    try:
        await wait_until(lambda: "assignment_result" in _types(ws))
    finally:
        await client.stop()
        await asyncio.wait_for(running, timeout=2.0)

    first = MessageEnvelope.model_validate_json(ws.frames[0])
    assert first.type == "hello"
    assert "devices" not in first.payload
    assert first.unwrap() == ClientHello(
        protocol_version=PROTOCOL_VERSION, client_id="test_client", site="test_site", lab="test_lab", workcell=None,
    )
    assert _types(ws).count("hello") == 1
