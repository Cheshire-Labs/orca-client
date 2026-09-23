"""The client waits the delays in `platform.reconnect_backoff` between failed dials."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from types import TracebackType
from typing import Literal, Optional, Type

import pytest
import websockets
from websockets.exceptions import ConnectionClosedError

from orca_client.client import WebSocketClient
from orca_client.config.models import ClientConfig, PlatformConfig
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor

Outcome = Literal["refused", "clean close", "dropped"]


class _OpenSocket:
    """A socket that opened, took the connect frame, then ended cleanly or dropped."""

    def __init__(self, dropped: bool) -> None:
        self._dropped = dropped

    async def send(self, message: str) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        if self._dropped:
            raise ConnectionClosedError(None, None)
        raise StopAsyncIteration

    async def close(self) -> None:
        return None


class _ScriptedDial:
    """Stands in for websockets.connect; each dial plays the next outcome, the last one repeats."""

    def __init__(self, script: list[Outcome]) -> None:
        self._script = script
        self.dials = 0

    def __call__(
        self,
        uri: str,
        *,
        ssl: Optional[bool] = None,
        additional_headers: Optional[dict[str, str]] = None,
    ) -> "_ScriptedDial":
        self.dials += 1
        return self

    async def __aenter__(self) -> _OpenSocket:
        outcome = self._script[min(self.dials, len(self._script)) - 1]
        if outcome == "refused":
            raise OSError("connection refused")
        return _OpenSocket(dropped=outcome == "dropped")

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        return False


async def _waits_for(
    script: list[Outcome],
    count: int,
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
) -> list[float]:
    """Run the client against the script and return the first `count` delays it waited."""
    platform = client_config.platform.model_copy(update={"reconnect_backoff": [1.0, 2.0, 3.0]})
    config = client_config.model_copy(update={"platform": platform})
    client = WebSocketClient(config=config, executor=CommandExecutor(registry=DeviceRegistry()))
    waited: list[float] = []

    async def record_the_wait(delay: float) -> None:
        waited.append(delay)
        await asyncio.sleep(0)

    monkeypatch.setattr(websockets, "connect", _ScriptedDial(script))
    monkeypatch.setattr(client, "_wait_before_retry", record_the_wait)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: len(waited) >= count)
    await client.stop()
    await asyncio.wait_for(running, timeout=2.0)
    return waited[:count]


async def test_failed_dials_wait_each_configured_delay_then_repeat_the_last(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    waited = await _waits_for(["refused"], 5, client_config, wait_until, monkeypatch)

    assert waited == [1.0, 2.0, 3.0, 3.0, 3.0]


@pytest.mark.parametrize("ending", ["clean close", "dropped"])
async def test_a_connection_that_opened_starts_the_delays_again(
    ending: Outcome,
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
):
    """Otherwise every restart of the runtime leaves the agent on the longest delay for good."""
    script: list[Outcome] = ["refused", "refused", ending, "refused"]

    waited = await _waits_for(script, 4, client_config, wait_until, monkeypatch)

    assert waited == [1.0, 2.0, 1.0, 2.0]


@pytest.mark.parametrize("backoff", [[], [0.0], [1.0, -1.0]])
def test_a_backoff_without_a_real_pause_is_refused(backoff: list[float]):
    with pytest.raises(ValueError, match="reconnect_backoff"):
        PlatformConfig(url="ws://127.0.0.1:1/ws/devices", api_key="k", reconnect_backoff=backoff)
