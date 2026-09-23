"""Tests for command executor."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from orca_client.executor import CommandCancelled, CommandExecutor
from cheshire_drivers.gateway_protocol import CommandMessage
from orca_client.devices import DeviceRegistry


@pytest.mark.asyncio
async def test_execute_valid_command(device_registry: DeviceRegistry):
    """Test executing a valid command."""
    executor = CommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_1",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )

    response = await executor.execute(cmd)

    assert response.success is True
    assert response.command_id == "test_1"
    assert response.error is None


@pytest.mark.asyncio
async def test_execute_device_not_found(device_registry: DeviceRegistry):
    """Test executing command on non-existent device."""
    executor = CommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_2",
        device_name="nonexistent",
        command="shake",
        params={},
        effective_mode="LIVE",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "DeviceNotFoundError"
    assert response.error is not None
    assert "not found" in response.error.lower()


@pytest.mark.asyncio
async def test_execute_unsupported_command(device_registry: DeviceRegistry):
    """Test executing unsupported command."""
    executor = CommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_3",
        device_name="test_shaker",
        command="fly",  # Shakers can't fly
        params={},
        effective_mode="LIVE",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "UnsupportedCommandError"


@pytest.mark.asyncio
async def test_execute_private_method(device_registry: DeviceRegistry):
    """Test that private methods are blocked."""
    executor = CommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_4",
        device_name="test_shaker",
        command="_private_method",
        params={},
        effective_mode="LIVE",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "SecurityError"
    assert response.error is not None
    assert "private" in response.error.lower()


@pytest.mark.asyncio
async def test_execute_invalid_parameters(device_registry: DeviceRegistry):
    """Test executing command with invalid parameters."""
    executor = CommandExecutor(device_registry)

    cmd = CommandMessage(
        command_id="test_5",
        device_name="test_shaker",
        command="shake",
        params={"invalid_param": "value"},  # Missing required params
        effective_mode="LIVE",
    )

    response = await executor.execute(cmd)

    assert response.success is False
    assert response.error_type == "ParameterError"


# The per-command-timeout-resolution tests were removed: orca-client
# no longer imposes a command duration bound. The engine (orca-core) owns the
# timeout (recoverable-timeout: an overrun is an operator decision, not a
# client failure) and cancels a command it no longer wants via the cancel
# message. Cancellation behavior is covered below.


@pytest.mark.asyncio
async def test_cancel_in_flight_command_raises_and_sends_no_response(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
):
    """A server cancel stops the running command; execute raises
    CommandCancelled so the caller suppresses the response."""
    executor = CommandExecutor(device_registry)
    shaker = device_registry.get_driver("test_shaker")

    started = asyncio.Event()

    async def blocking_shake(request):  # noqa: ANN001 - test stub
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(shaker, "shake", blocking_shake)

    cmd = CommandMessage(
        command_id="cancel_me",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )
    task = asyncio.ensure_future(executor.execute(cmd))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    assert await executor.cancel("cancel_me") is True
    with pytest.raises(CommandCancelled):
        await task
    # Cancelling an already-finished / unknown command is a no-op.
    assert await executor.cancel("cancel_me") is False
    assert await executor.cancel("never_existed") is False


@pytest.mark.asyncio
async def test_shutdown_cancel_drains_driver_before_releasing_lock(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
):
    """A non-server cancel (client shutdown cancels the handler task directly)
    must cancel + drain the driver task before the device lock releases, so the
    driver is never left running while the next command acquires the lock."""
    executor = CommandExecutor(device_registry)
    shaker = device_registry.get_driver("test_shaker")

    started = asyncio.Event()
    driver_cancelled = asyncio.Event()

    async def blocking_shake(request):  # noqa: ANN001 - test stub
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            driver_cancelled.set()
            raise

    monkeypatch.setattr(shaker, "shake", blocking_shake)

    cmd = CommandMessage(
        command_id="shutdown_me",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )
    task = asyncio.ensure_future(executor.execute(cmd))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    # Shutdown: cancel the handler task itself (NOT executor.cancel()).
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The driver task was cancelled + drained before the lock released; without
    # the fix the driver keeps running and this event never fires (the drain
    # happens inside the `async with driver.lock` block, before it exits).
    await asyncio.wait_for(driver_cancelled.wait(), timeout=2.0)


@pytest.mark.asyncio
async def test_cancel_while_waiting_on_device_lock_suppresses_response(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
    wait_until: Callable[..., Awaitable[None]],
):
    """A cancel that arrives while the command is still queued on the device
    lock (before its driver task registers) must suppress the response and
    never invoke the driver. The lock wait can be minutes on real hardware, so
    this is the wide race window, not a micro one.

    Driven through the public API only: a holder command occupies the device
    lock; the second command queues on it; we cancel the second while it waits
    and assert its driver body never runs (call_count stays 1)."""
    executor = CommandExecutor(device_registry)
    shaker = device_registry.get_driver("test_shaker")

    call_count = 0
    holder_started = asyncio.Event()

    async def blocking_shake(request):  # noqa: ANN001 - test stub
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            holder_started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(shaker, "shake", blocking_shake)

    base = {
        "device_name": "test_shaker",
        "command": "shake",
        "params": {"speed": 300.0, "duration": 5.0},
        "effective_mode": "LIVE",
    }
    holder = CommandMessage(command_id="holder", **base)
    waiter = CommandMessage(command_id="wait_then_cancel", **base)

    holder_task = asyncio.ensure_future(executor.execute(holder))
    await asyncio.wait_for(holder_started.wait(), timeout=2.0)  # holds the lock

    waiter_task = asyncio.ensure_future(executor.execute(waiter))
    # Wait until the waiter has claimed the cache (then it is parked on the
    # holder's device lock). Condition-driven, not a wall-clock guess.
    await wait_until(
        lambda: executor._idempotency_cache.is_inflight("wait_then_cancel")
    )

    # Waiter has no driver task yet, but is claimed in the cache, so the cancel
    # tombstones it (returns False: nothing interrupted).
    assert await executor.cancel("wait_then_cancel") is False

    # Free the lock by cancelling the holder; the waiter then acquires it, sees
    # its tombstone, and raises without ever invoking the driver.
    assert await executor.cancel("holder") is True
    with pytest.raises(CommandCancelled):
        await holder_task
    with pytest.raises(CommandCancelled):
        await waiter_task
    assert call_count == 1, "waiter must not run; only the holder's shake executed"


@pytest.mark.asyncio
async def test_cancelled_command_replay_is_suppressed(
    device_registry: DeviceRegistry, monkeypatch: pytest.MonkeyPatch,
):
    """A server cancel is terminal: a gateway replay of the same command_id
    within the idempotency TTL re-raises CommandCancelled rather than
    re-running the cancelled command."""
    executor = CommandExecutor(device_registry)
    shaker = device_registry.get_driver("test_shaker")

    started = asyncio.Event()
    call_count = 0

    async def blocking_shake(request):  # noqa: ANN001 - test stub
        nonlocal call_count
        call_count += 1
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(shaker, "shake", blocking_shake)

    cmd = CommandMessage(
        command_id="replay_me",
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )
    task = asyncio.ensure_future(executor.execute(cmd))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    assert await executor.cancel("replay_me") is True
    with pytest.raises(CommandCancelled):
        await task

    # Gateway resends the same command_id after a reconnect: it must NOT run
    # the cancelled command again.
    with pytest.raises(CommandCancelled):
        await executor.execute(cmd)
    assert call_count == 1, "cancelled command must not re-run on replay"
