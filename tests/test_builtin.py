"""The built-in client is the ordinary client, configured by the server that starts it."""

import asyncio
import os
import subprocess
import sys
from http import HTTPStatus
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
import websockets
from websockets.asyncio.server import ServerConnection
from websockets.http11 import Request, Response

from orca_client.builtin import builtin_client_config
from orca_client.config.logging_setup import setup_logging


def test_its_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCA_CLIENT_URL", "ws://127.0.0.1:8765/ws/devices")
    monkeypatch.setenv("ORCA_CLIENT_API_KEY", "k")

    config = builtin_client_config()

    assert config.client_id == "builtin"
    assert config.platform.url == "ws://127.0.0.1:8765/ws/devices"
    assert config.platform.api_key == "k"


def test_running_it_by_hand_says_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCA_CLIENT_URL", raising=False)

    with pytest.raises(ValueError, match="started by an Orca server"):
        builtin_client_config()


async def test_a_client_that_gives_up_with_stdin_still_open_exits_cleanly() -> None:
    """A server that refuses the client outright makes it give up while the stdin thread is still reading."""

    def refuse(connection: ServerConnection, request: Request) -> Response:
        return connection.respond(HTTPStatus.FORBIDDEN, "refused")

    async with websockets.serve(lambda ws: ws.close(), "127.0.0.1", 0, process_request=refuse) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "orca_client", "--builtin",
            stdin=asyncio.subprocess.PIPE,
            env={**os.environ, "ORCA_CLIENT_URL": f"ws://127.0.0.1:{port}/ws/devices", "ORCA_CLIENT_API_KEY": "k"},
        )

        assert await asyncio.wait_for(process.wait(), timeout=60) == 0


def test_the_built_in_client_logs_to_its_own_file_so_it_never_shares_a_lab_clients_log() -> None:
    logger = setup_logging(log_name="builtin-client.log")

    files = [h.baseFilename for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert files and all(Path(f).name == "builtin-client.log" for f in files)


def test_closing_stdin_ends_the_process_cleanly() -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "orca_client", "--builtin"],
        stdin=subprocess.PIPE,
        env={**os.environ, "ORCA_CLIENT_URL": "ws://127.0.0.1:9/ws/devices", "ORCA_CLIENT_API_KEY": "k"},
    )
    assert process.stdin is not None
    process.stdin.close()

    assert process.wait(timeout=30) == 0


async def test_stdin_eof_calls_stop_on_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from orca_client import builtin

    read, write = os.pipe()
    monkeypatch.setattr(builtin.sys, "stdin", os.fdopen(read, "r"))
    stopped = asyncio.Event()

    async def stop() -> None:
        stopped.set()

    builtin.stop_when_stdin_closes(stop)
    os.close(write)

    await asyncio.wait_for(stopped.wait(), timeout=5.0)
