"""Running as the server's built-in client: the same program, configured by its parent process."""

import asyncio
import os
import sys
import threading
from collections.abc import Awaitable, Callable

from cheshire_drivers.device_setup import BUILTIN_CLIENT

from .config import ClientConfig, PlatformConfig


def builtin_client_config() -> ClientConfig:
    if "ORCA_CLIENT_URL" not in os.environ or "ORCA_CLIENT_API_KEY" not in os.environ:
        raise ValueError("--builtin is started by an Orca server, which sets ORCA_CLIENT_URL and ORCA_CLIENT_API_KEY.")
    return ClientConfig(
        client_id=BUILTIN_CLIENT,
        site="local",
        lab="built-in",
        platform=PlatformConfig(
            url=os.environ["ORCA_CLIENT_URL"],
            api_key=os.environ["ORCA_CLIENT_API_KEY"],
            reconnect_backoff=[0.2, 0.5, 1.0, 2.0],
        ),
    )


def stop_when_stdin_closes(stop: Callable[[], Awaitable[None]]) -> None:
    """A thread reads stdin because the Windows event loop cannot wait on it. EOF means the parent is done."""
    loop = asyncio.get_running_loop()

    def wait_for_eof() -> None:
        # os.read, not sys.stdin: interpreter exit aborts while a daemon thread is blocked in a buffered read.
        descriptor = sys.stdin.fileno()
        while os.read(descriptor, 4096):
            pass
        asyncio.run_coroutine_threadsafe(_call(stop), loop)

    threading.Thread(target=wait_for_eof, name="orca-client-stdin", daemon=True).start()


async def _call(stop: Callable[[], Awaitable[None]]) -> None:
    await stop()
