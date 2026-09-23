"""One run of the client: build the drivers, run, and always release them.

The release half is the point: drivers hold serial ports and vendor
simulator subprocesses, so an exit that skips cleanup leaves them behind.
"""

import json
from pathlib import Path

import pytest

from orca_client.config.models import ClientConfig
from orca_client.session import ClientSession


async def test_session_registers_every_configured_device(client_config: ClientConfig):
    session = ClientSession(client_config)

    assert set(session.registry.get_all_device_ids()) == {"test_shaker", "test_centrifuge"}


async def test_session_wires_the_executor_to_its_own_registry(client_config: ClientConfig):
    session = ClientSession(client_config)

    assert session.executor.registry is session.registry


async def test_run_releases_drivers_when_the_client_stops(
    client_config: ClientConfig, monkeypatch: pytest.MonkeyPatch
):
    session = ClientSession(client_config)
    released: list[str] = []

    async def start() -> None:
        return None

    async def cleanup_all() -> None:
        released.append("cleaned")

    monkeypatch.setattr(session.client, "start", start)
    monkeypatch.setattr(session.registry, "cleanup_all", cleanup_all)

    await session.run()

    assert released == ["cleaned"]


async def test_run_releases_drivers_when_the_client_raises(
    client_config: ClientConfig, monkeypatch: pytest.MonkeyPatch
):
    session = ClientSession(client_config)
    released: list[str] = []

    async def start() -> None:
        raise RuntimeError("connection died")

    async def cleanup_all() -> None:
        released.append("cleaned")

    monkeypatch.setattr(session.client, "start", start)
    monkeypatch.setattr(session.registry, "cleanup_all", cleanup_all)

    with pytest.raises(RuntimeError, match="connection died"):
        await session.run()

    assert released == ["cleaned"]


async def test_from_config_path_builds_a_session_from_disk(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "client_id": "bench-01",
                "site": "test",
                "lab": "simulation",
                "platform": {"url": "wss://orca.example.com/ws/devices", "api_key": "k"},
                "devices": [
                    {"type": "shaker", "name": "shaker_1", "driver": {"type": "sim"}}
                ],
            }
        )
    )

    session = ClientSession.from_config_path(config_path)

    assert session.config.client_id == "bench-01"
    assert session.registry.get_all_device_ids() == ["shaker_1"]
