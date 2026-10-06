"""A URL websockets refuses stops the client rather than going round the redial loop.

The scheme is checked when the config loads. What is left is a URL that reads
like a WebSocket URL and websockets still refuses: a fragment, a username with
no password. Nothing the runtime does can make one of those work, so a redial
is a loop with no way out of it.
"""

import asyncio

from orca_client.client import WebSocketClient
from orca_client.config.models import ClientConfig, PlatformConfig
from orca_client.devices import DeviceRegistry
from orca_client.executor import CommandExecutor


def _client_dialing(url: str, client_config: ClientConfig) -> WebSocketClient:
    platform = PlatformConfig(url=url, api_key="k", reconnect_backoff=[0.01])
    return WebSocketClient(
        config=client_config.model_copy(update={"platform": platform}),
        executor=CommandExecutor(registry=DeviceRegistry()),
    )


async def test_a_url_websockets_refuses_stops_the_client_and_says_what_to_fix(
    client_config: ClientConfig,
):
    """Nothing is monkeypatched: websockets rejects this URI before it opens a socket."""
    client = _client_dialing("ws://127.0.0.1:8765/ws/devices#devices", client_config)

    await asyncio.wait_for(client.start(), timeout=2.0)

    assert client.is_running is False
    assert client.stopped_because is not None
    assert "fragment" in client.stopped_because
    assert "platform.url" in client.stopped_because
