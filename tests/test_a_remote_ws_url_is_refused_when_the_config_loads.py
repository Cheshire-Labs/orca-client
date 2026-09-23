"""A plain ws:// URL to another host is refused when the config loads, so the agent never starts.

The check used to run inside each dial, where a failure counts as a dropped
connection: the agent logged it and redialed forever.
"""

import json
from pathlib import Path

import pytest

from orca_client.config import load_config


def _config(tmp_path: Path, url: str) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "client_id": "bench-01",
        "site": "test",
        "lab": "simulation",
        "platform": {"url": url, "api_key": "k"},
        "devices": [],
    }))
    return str(path)


def test_a_ws_url_to_another_host_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Use wss:// for 192.0.2.10"):
        load_config(_config(tmp_path, "ws://192.0.2.10:8765/ws/devices"))


@pytest.mark.parametrize("url", [
    "ws://localhost:8765/ws/devices",
    "ws://127.0.0.1:8765/ws/devices",
    "ws://[::1]:8765/ws/devices",
    "wss://gateway.example.com/ws/devices",
])
def test_ws_to_this_machine_and_wss_to_any_host_load(tmp_path: Path, url: str) -> None:
    assert load_config(_config(tmp_path, url)).platform.url == url
