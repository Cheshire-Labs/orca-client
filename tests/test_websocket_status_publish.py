"""The agent has to actually put its status report on the wire.

What this replaced was a status helper with no callers at all: the platform had
nothing to read and every device rendered offline while the agent answered
commands normally. Deleting a publish call is silent, so both call sites are
pinned here rather than only the report that feeds them.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Optional

import pytest

from cheshire_drivers.gateway_protocol import CommandMessage, MessageEnvelope
from orca_client.client.websocket_client import WebSocketClient
from orca_client.config.models import ClientConfig
from orca_client.devices import DeviceRegistry
from cheshire_drivers import SimShakerDriver
from orca_client.executor import CommandCancelled, CommandExecutor


class _CapturingWs:
    """Records every frame the client sends."""

    def __init__(self) -> None:
        self.frames: list[str] = []

    async def send(self, message: str) -> None:
        self.frames.append(message)


def _frame_types(ws: _CapturingWs) -> list[str]:
    return [json.loads(frame)["type"] for frame in ws.frames]


def _connected_client(
    config: ClientConfig,
    registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
    heartbeat_interval: Optional[float] = None,
) -> tuple[WebSocketClient, _CapturingWs]:
    if heartbeat_interval is not None:
        platform = config.platform.model_copy(update={"heartbeat_interval": heartbeat_interval})
        config = config.model_copy(update={"platform": platform})
    client = WebSocketClient(config, CommandExecutor(registry))
    ws = _CapturingWs()
    monkeypatch.setattr(client, "ws", ws)
    monkeypatch.setattr(client, "_connected", True)
    return client, ws


def _bring_up_command() -> MessageEnvelope:
    return MessageEnvelope.wrap_command(
        CommandMessage(
            command_id="cmd_1",
            device_name="test_shaker",
            command="initialize",
            params={},
            effective_mode="LIVE",
        )
    )


def _registry_awaiting_bring_up() -> DeviceRegistry:
    """A shaker that has not been brought up, so a command has something to move."""
    registry = DeviceRegistry()
    registry.register(
        name="test_shaker", device_type="shaker", driver=SimShakerDriver("test_shaker"),
    )
    return registry


@pytest.mark.asyncio
async def test_every_heartbeat_carries_a_status_report(
    client_config: ClientConfig,
    device_registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
    wait_until: Callable[..., Awaitable[None]],
) -> None:
    """Heartbeats are the only cadence a device that nobody commanded gets."""
    client, ws = _connected_client(client_config, device_registry, monkeypatch)

    beating = asyncio.create_task(client._heartbeat_loop())
    try:
        await wait_until(lambda: "status" in _frame_types(ws))
    finally:
        beating.cancel()
        with pytest.raises(asyncio.CancelledError):
            await beating

    assert _frame_types(ws)[:2] == ["heartbeat", "status"]


@pytest.mark.asyncio
async def test_a_completed_command_reports_before_it_answers(
    client_config: ClientConfig,
    device_registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The platform reads this socket in order and resolves the caller's future
    off the response, so a response sent first hands back success while the flag
    that command just moved is still unread: the operator connects a device and
    the very next registry read still says disconnected.
    """
    client, ws = _connected_client(
        client_config, _registry_awaiting_bring_up(), monkeypatch,
    )

    await client._handle_command(_bring_up_command())

    assert _frame_types(ws) == ["status", "response"]
    reported = json.loads(ws.frames[0])["payload"]["devices"]
    assert reported["test_shaker"]["links"]["LIVE"]["is_connected"] is True


@pytest.mark.asyncio
async def test_a_cancelled_command_still_reports_what_the_driver_was_left_in(
    client_config: ClientConfig,
    device_registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server-side abort stops the driver part-way, which is exactly when its
    state is least predictable. No response goes out, so the report is the only
    thing that tells the platform where the device ended up.
    """
    client, ws = _connected_client(client_config, device_registry, monkeypatch)

    async def cancelled_by_the_server(cmd: CommandMessage) -> None:
        raise CommandCancelled(cmd.command_id)

    monkeypatch.setattr(client.executor, "execute", cancelled_by_the_server)

    await client._handle_command(_bring_up_command())

    assert _frame_types(ws) == ["status"]


@pytest.mark.asyncio
async def test_a_command_that_fails_outside_the_executor_still_reports_first(
    client_config: ClientConfig,
    device_registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executor reshapes driver errors into a failed response itself, so
    this branch is the odd outcome (a malformed command, a duplicate id whose
    cached failure re-raises). It answers like every other one, so it has to
    report like every other one or the ordering rule has a hole.
    """
    client, ws = _connected_client(client_config, device_registry, monkeypatch)

    async def blew_up_before_the_executor_could_reshape_it(cmd: CommandMessage) -> None:
        raise RuntimeError("idempotency cache re-raised")

    monkeypatch.setattr(
        client.executor, "execute", blew_up_before_the_executor_could_reshape_it,
    )

    await client._handle_command(_bring_up_command())

    assert _frame_types(ws) == ["status", "response"]
    assert json.loads(ws.frames[1])["payload"]["success"] is False


@pytest.mark.asyncio
async def test_a_report_that_raises_does_not_stop_the_heartbeats(
    client_config: ClientConfig,
    device_registry: DeviceRegistry,
    monkeypatch: pytest.MonkeyPatch,
    wait_until: Callable[..., Awaitable[None]],
) -> None:
    """Publishing runs inside the heartbeat task, so an escaping error would end
    that task: the platform would then see the client as gone and drop every
    device, on nothing worse than one unreadable driver.
    """
    client, ws = _connected_client(
        client_config, device_registry, monkeypatch, heartbeat_interval=0.0,
    )

    def cannot_be_built() -> None:
        raise RuntimeError("driver property blew up")

    monkeypatch.setattr(client.executor.registry, "status_report", cannot_be_built)

    beating = asyncio.create_task(client._heartbeat_loop())
    try:
        await wait_until(lambda: _frame_types(ws).count("heartbeat") >= 3)
    finally:
        beating.cancel()
        with pytest.raises(asyncio.CancelledError):
            await beating

    assert "status" not in _frame_types(ws)
