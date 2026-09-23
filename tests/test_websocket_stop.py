"""Stopping the client must always hand control back so the devices get released.

A caller releases devices only after ``start`` returns, so anything that makes
``stop`` hang or raise leaves real hardware held open.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from types import TracebackType
from typing import Optional, Type

import pytest
import websockets
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from orca_client.client import WebSocketClient
from orca_client.config.models import ClientConfig
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


def _client(client_config: ClientConfig, reconnect_delay: float = 30.0) -> WebSocketClient:
    platform = client_config.platform.model_copy(update={"reconnect_backoff": [reconnect_delay]})
    return WebSocketClient(
        config=client_config.model_copy(update={"platform": platform}),
        executor=CommandExecutor(registry=DeviceRegistry()),
    )


async def test_stop_returns_from_start_while_it_is_backing_off(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    client = _client(client_config)
    attempts = 0

    async def refuse_the_connection() -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("connection refused")

    monkeypatch.setattr(client, "_connect_and_run", refuse_the_connection)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: attempts >= 1)

    await client.stop()

    await asyncio.wait_for(running, timeout=2.0)
    assert attempts == 1, "backoff should not have retried after stop"


async def test_stop_is_safe_before_start_has_backed_off(client_config: ClientConfig):
    """Quitting right after launch must not raise or hang."""
    client = _client(client_config)

    await client.stop()

    assert client.is_running is False


async def test_stop_swallows_a_receive_task_that_already_died(client_config: ClientConfig):
    """The platform closing the socket leaves an unretrieved ConnectionClosed.

    Awaiting that task re-raises it. If stop() lets it out, the caller never
    reaches its device cleanup and the drivers stay held while the UI reports a
    clean exit.
    """
    client = _client(client_config)

    async def receive_that_died() -> None:
        raise ConnectionClosedError(Close(1001, "going away"), None)

    task = asyncio.create_task(receive_that_died())
    await asyncio.sleep(0)
    assert task.done() and not task.cancelled()
    client._receive_task = task

    await client.stop()

    assert client.is_running is False


async def test_stop_swallows_a_heartbeat_task_that_already_died(client_config: ClientConfig):
    """Same hazard on the other per-connection task."""
    client = _client(client_config)

    async def heartbeat_that_died() -> None:
        raise ConnectionClosedError(Close(1006, "abnormal"), None)

    task = asyncio.create_task(heartbeat_that_died())
    await asyncio.sleep(0)
    client._heartbeat_task = task

    await client.stop()

    assert client.is_running is False


async def test_stop_cancels_a_dial_in_flight(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    """Quitting mid-dial must not wait out the connect timeout holding devices.

    Asserted on task state rather than through ``wait_for``: a timeout there
    cancels ``start``, which catches CancelledError and returns normally, so
    ``wait_for`` reports success and the assertion passes either way.
    """
    client = _client(client_config)
    dialing = False

    async def dial_forever() -> None:
        nonlocal dialing
        dialing = True
        await asyncio.sleep(3600)

    monkeypatch.setattr(client, "_connect_and_run", dial_forever)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: dialing)

    await client.stop()

    for _ in range(10):
        if running.done():
            break
        await asyncio.sleep(0)

    assert running.done(), "stop() left the dial running; quitting would wait it out"
    await running


async def test_reconnect_cuts_a_backoff_short(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    """Reconnect during a backoff must redial, not sit out the remaining delay."""
    client = _client(client_config, reconnect_delay=30.0)
    attempts = 0

    async def refuse_the_connection() -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("connection refused")

    monkeypatch.setattr(client, "_connect_and_run", refuse_the_connection)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: attempts >= 1)

    await client.reconnect()

    await wait_until(lambda: attempts >= 2, timeout=2.0)
    await client.stop()
    await asyncio.wait_for(running, timeout=2.0)


async def test_a_permanent_refusal_records_why_for_the_operator(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    """A bad API key must say so, not read as an ordinary stop."""
    client = _client(client_config)

    async def refused_permanently() -> None:
        raise ConnectionClosedError(Close(1008, "bad key"), None)

    monkeypatch.setattr(client, "_connect_and_run", refused_permanently)

    await asyncio.wait_for(client.start(), timeout=2.0)

    assert client.is_running is False
    assert client.stopped_because is not None
    assert "API key" in client.stopped_because


class _RefusedBeforeOpening:
    """Stands in for websockets.connect when the server answers the handshake with an HTTP error."""

    def __init__(self, status: int) -> None:
        self._status = status
        self.dials = 0

    def __call__(
        self,
        uri: str,
        *,
        ssl: Optional[bool] = None,
        additional_headers: Optional[dict[str, str]] = None,
    ) -> "_RefusedBeforeOpening":
        self.dials += 1
        return self

    async def __aenter__(self) -> None:
        raise InvalidStatus(Response(self._status, "", Headers()))

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        return False


async def test_a_key_refused_before_the_socket_opens_stops_the_client_and_says_so(
    client_config: ClientConfig,
    monkeypatch: pytest.MonkeyPatch,
):
    """The Orca gateway closes before accepting a bad key, which reaches the client as HTTP 403.

    Treated as a dropped link, that redials forever and never tells the operator the key is wrong.
    """
    client = _client(client_config, reconnect_delay=0.01)
    dial = _RefusedBeforeOpening(403)
    monkeypatch.setattr(websockets, "connect", dial)

    await asyncio.wait_for(client.start(), timeout=5.0)

    assert client.is_running is False
    assert dial.dials == 1, "a rejected key must not be redialed"
    assert client.stopped_because is not None
    assert "API key" in client.stopped_because


async def test_a_server_error_before_the_socket_opens_is_redialed(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    """A 503 while the runtime is still starting is transient, so the client keeps dialing."""
    client = _client(client_config, reconnect_delay=0.01)
    dial = _RefusedBeforeOpening(503)
    monkeypatch.setattr(websockets, "connect", dial)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: dial.dials >= 2, timeout=2.0)

    assert client.stopped_because is None
    await client.stop()
    await asyncio.wait_for(running, timeout=2.0)


async def test_a_running_client_has_no_stop_reason(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    client = _client(client_config)

    async def refuse_the_connection() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(client, "_connect_and_run", refuse_the_connection)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: client.is_running)

    assert client.stopped_because is None

    await client.stop()
    await asyncio.wait_for(running, timeout=2.0)


class _SocketThatEndsCleanly:
    """A platform that reads the connect frame and then ends the stream.

    No exception reaches the client, which is the case a backoff keyed only to
    the except branches never covers.
    """

    async def send(self, message: str) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration

    async def close(self) -> None:
        return None


class _SocketClosedAfterTheHandshake(_SocketThatEndsCleanly):
    """A platform that reads the connect frame and then refuses the connection."""

    def __init__(self, close: Close) -> None:
        self._close = close

    async def __anext__(self) -> str:
        raise ConnectionClosedError(self._close, None)


class _CountingDial:
    """Stands in for websockets.connect and counts how often it is dialed."""

    def __init__(self, ws: _SocketThatEndsCleanly) -> None:
        self._ws = ws
        self.dials = 0

    def __call__(
        self,
        uri: str,
        *,
        ssl: Optional[bool] = None,
        additional_headers: Optional[dict[str, str]] = None,
    ) -> "_CountingDial":
        self.dials += 1
        return self

    async def __aenter__(self) -> _SocketThatEndsCleanly:
        return self._ws

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        return False


async def test_a_refused_protocol_version_stops_the_client_and_says_so(
    client_config: ClientConfig,
    monkeypatch: pytest.MonkeyPatch,
):
    """The platform refuses a mismatched version by closing 1002 AFTER the
    handshake, so the refusal arrives as an exception inside the receive task.
    Leaving it unretrieved turns a permanent refusal into an unbacked-off redial
    storm with nothing in the operator's log naming the version.
    """
    client = _client(client_config, reconnect_delay=0.01)
    dial = _CountingDial(
        _SocketClosedAfterTheHandshake(
            Close(1002, "Incompatible protocol version: expected 1.1.0")
        )
    )
    monkeypatch.setattr(websockets, "connect", dial)

    await asyncio.wait_for(client.start(), timeout=5.0)

    assert client.is_running is False
    assert dial.dials == 1, "a permanent refusal must not be redialed"
    assert client.stopped_because is not None
    assert "1.1.0" in client.stopped_because


async def test_a_clean_close_is_backed_off_rather_than_redialed_on_the_spot(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    """A socket that ends without raising leaves the dial loop nothing to catch.

    The backoff used to live only in the except branches, so this path fell
    straight back into a redial: an ordinary clean close became a reconnect
    storm against a platform that had done nothing wrong. Waiting is only half
    of it. Giving up would also stop the storm, and would leave the lab with an
    agent that went dark on a close the platform is entitled to send, so the
    client has to still be dialing when it is asked to.
    """
    client = _client(client_config, reconnect_delay=30.0)
    dial = _CountingDial(_SocketThatEndsCleanly())
    monkeypatch.setattr(websockets, "connect", dial)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: dial.dials >= 1)
    for _ in range(50):
        await asyncio.sleep(0)

    assert dial.dials == 1, "a clean close redialed without waiting"
    assert not running.done(), "a clean close ended the client instead of waiting"
    assert client.is_running is True
    assert client.stopped_because is None

    await client.reconnect()
    await wait_until(lambda: dial.dials >= 2, timeout=2.0)

    await client.stop()
    await asyncio.wait_for(running, timeout=2.0)
