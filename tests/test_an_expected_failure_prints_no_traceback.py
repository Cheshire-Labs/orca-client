"""A failure with a message the operator can act on prints that message, alone.

The trace for these is fifteen frames of asyncio and websockets internals. It
buries the one line that says what to do, and it printed on defaults, with no
--verbose asked for.
"""

import asyncio
import logging
from argparse import Namespace
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from orca_client.__main__ import async_main
from orca_client.client import WebSocketClient
from orca_client.config.models import ClientConfig
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


def _errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def _client(client_config: ClientConfig) -> WebSocketClient:
    platform = client_config.platform.model_copy(update={"reconnect_backoff": [0.01]})
    return WebSocketClient(
        config=client_config.model_copy(update={"platform": platform}),
        executor=CommandExecutor(registry=DeviceRegistry()),
    )


async def test_a_config_file_that_is_not_there_prints_its_message_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="orca_client")

    exit_code = await async_main(Namespace(config=tmp_path / "config.json"))

    assert exit_code == 1
    [refused] = _errors(caplog)
    assert "Configuration file not found" in refused.getMessage()
    assert refused.exc_info is None, "the traceback buried the message again"


async def test_a_config_the_models_reject_prints_its_message_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """A config that loads and fails validation is the same class of failure."""
    caplog.set_level(logging.INFO, logger="orca_client")
    config = tmp_path / "config.json"
    config.write_text(
        '{"client_id": "c", "site": "s", "lab": "l", "devices": [],'
        ' "platform": {"url": "http://127.0.0.1:8765/ws/devices", "api_key": "k"}}'
    )

    exit_code = await async_main(Namespace(config=config))

    assert exit_code == 1
    [refused] = _errors(caplog)
    assert "must start with ws:// or wss://" in refused.getMessage()
    assert refused.exc_info is None


async def test_verbose_still_gets_the_trace(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """The trace is debug detail, not noise on by default."""
    caplog.set_level(logging.DEBUG, logger="orca_client")

    await async_main(Namespace(config=tmp_path / "config.json"))

    [refused] = _errors(caplog)
    assert refused.exc_info is not None


async def test_a_runtime_that_is_not_listening_is_one_line_and_another_dial(
    client_config: ClientConfig,
    wait_until: Callable[..., Awaitable[None]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Forgetting to start the runtime is the common case, and it is transient.

    The bridge is meant to outlive a runtime that restarts, so it keeps dialing.
    What it must not do is print a stack trace every second while it waits.
    """
    caplog.set_level(logging.INFO, logger="orca_client")
    client = _client(client_config)
    dials = 0

    async def refuse_the_connection() -> None:
        nonlocal dials
        dials += 1
        raise ConnectionRefusedError(
            "[WinError 1225] The remote computer refused the network connection"
        )

    monkeypatch.setattr(client, "_connect_and_run", refuse_the_connection)

    running = asyncio.create_task(client.start())
    await wait_until(lambda: dials >= 2)
    await client.stop()
    await asyncio.wait_for(running, timeout=2.0)

    assert client.stopped_because is None, "a runtime that is not up yet is transient"
    reported = _errors(caplog)
    assert reported, "the operator was told nothing"
    assert "Is it running?" in reported[0].getMessage()
    assert all(record.exc_info is None for record in reported)


async def test_a_socket_that_closes_mid_heartbeat_prints_no_trace(
    client_config: ClientConfig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """A runtime restarting while the bridge is connected fails the next heartbeat."""
    caplog.set_level(logging.INFO, logger="orca_client")
    client = _client(client_config)

    class _ClosedOnSend:
        async def send(self, message: str) -> None:
            raise ConnectionClosedError(Close(1001, "going away"), None)

    monkeypatch.setattr(client, "ws", _ClosedOnSend())
    monkeypatch.setattr(client, "_connected", True)

    with pytest.raises(ConnectionClosedError):
        await client._heartbeat_loop()

    assert _errors(caplog) == [], "an ordinary close was reported as a client fault"
    assert any(
        "Connection closed while sending a heartbeat" in record.getMessage()
        for record in caplog.records
    )
