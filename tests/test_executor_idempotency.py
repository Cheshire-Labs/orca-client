"""Tests for command_id idempotency in the CommandExecutor.

The gateway can resend the same command on reconnect after a transient
WebSocket drop. orca-client must de-duplicate on command_id so side-effecting
driver commands run at most once. Three behaviors are pinned here:

1. In-flight duplicate: a second arrival while the first is still executing
   awaits the same future and returns the same response without re-invoking
   the driver method.
2. Completed cache: a re-arrival after the first response was sent returns
   the cached ResponseMessage without re-invoking the driver method.
3. TTL eviction: after the cache TTL elapses, the same command_id is treated
   as fresh and the driver method runs again (cache must not block legitimate
   command_id reuse after a long enough gap).
"""

import asyncio
from typing import Any, Dict, List

import pytest

from orca_client.executor import CommandExecutor
from orca_client.executor.idempotency_cache import CommandIdempotencyCache
from orca_client.devices import DeviceRegistry
from cheshire_drivers.gateway_protocol import CommandMessage


class _CallCountingShaker:
    """Minimal async driver stub that counts shake() invocations and lets the
    test control when each invocation completes via an injected gate event.
    """

    interfaces = frozenset({"IShaker"})

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.shake_calls: int = 0
        self._gates: List[asyncio.Event] = []

    def queue_gate(self, gate: asyncio.Event) -> None:
        """Each subsequent shake() call awaits the next queued gate before
        returning. Tests use this to hold a call mid-execution."""
        self._gates.append(gate)

    async def shake(self, request: Any) -> None:
        self.shake_calls += 1
        if self._gates:
            gate = self._gates.pop(0)
            await gate.wait()


def _make_executor_with_stub(driver: _CallCountingShaker, cache: CommandIdempotencyCache) -> CommandExecutor:
    registry = DeviceRegistry()
    # bypass register() so we don't pull in a real device factory + sim pairing
    registry._drivers["test_shaker"] = driver  # type: ignore[arg-type]
    registry._locks["test_shaker"] = driver.lock
    registry._device_types["test_shaker"] = "shaker"
    registry._device_names["test_shaker"] = "test_shaker"
    return CommandExecutor(registry, idempotency_cache=cache)


def _shake_command(command_id: str) -> CommandMessage:
    return CommandMessage(
        command_id=command_id,
        device_name="test_shaker",
        command="shake",
        params={"speed": 300.0, "duration": 5.0},
        effective_mode="LIVE",
    )


@pytest.mark.asyncio
async def test_in_flight_duplicate_awaits_single_invocation() -> None:
    """While the first execution is still running, a second arrival with the
    same command_id must NOT invoke the driver again. Both inbound resolve to
    the same single response."""
    driver = _CallCountingShaker()
    gate = asyncio.Event()
    driver.queue_gate(gate)

    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("dup_in_flight")

    # Kick off the first execution; it will block on the gate inside shake().
    first_task = asyncio.create_task(executor.execute(cmd))
    # Yield enough turns to let the executor enter shake() and hit the gate.
    for _ in range(10):
        await asyncio.sleep(0)
        if driver.shake_calls == 1:
            break
    assert driver.shake_calls == 1, "first call did not enter the driver"

    # Second arrival of the SAME command_id while the first is still in-flight.
    second_task = asyncio.create_task(executor.execute(cmd))
    # Give the second task scheduler turns; it must NOT start a second shake().
    for _ in range(10):
        await asyncio.sleep(0)
    assert driver.shake_calls == 1, "duplicate command_id triggered a second driver invocation"

    # Release the gate so the original call completes.
    gate.set()

    first_response = await first_task
    second_response = await second_task

    assert driver.shake_calls == 1
    assert first_response.success is True
    assert second_response.success is True
    assert first_response.command_id == "dup_in_flight"
    assert second_response.command_id == "dup_in_flight"
    # The two responses must be the identical payload (the executor satisfies
    # both inbound from the same single execution).
    assert first_response.model_dump() == second_response.model_dump()


@pytest.mark.asyncio
async def test_completed_command_id_returns_cached_response() -> None:
    """After the first execution completes, a re-arrival of the same
    command_id returns the cached ResponseMessage and does NOT invoke the
    driver method a second time."""
    driver = _CallCountingShaker()
    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("dup_completed")

    first_response = await executor.execute(cmd)
    assert first_response.success is True
    assert driver.shake_calls == 1

    # Resend the same command_id. Driver must NOT be invoked again.
    second_response = await executor.execute(cmd)
    assert driver.shake_calls == 1, "completed command_id triggered re-execution"
    assert second_response.model_dump() == first_response.model_dump()


@pytest.mark.asyncio
async def test_ttl_expiry_allows_reuse() -> None:
    """After the cache TTL elapses, the same command_id is treated as fresh
    and the driver method runs again."""
    driver = _CallCountingShaker()
    # Injectable clock so the test does not actually sleep for the TTL.
    now = [1000.0]
    cache = CommandIdempotencyCache(
        ttl_seconds=0.05,
        time_source=lambda: now[0],
    )
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("reused_after_ttl")

    first_response = await executor.execute(cmd)
    assert first_response.success is True
    assert driver.shake_calls == 1

    # Advance the injected clock beyond TTL.
    now[0] += 10.0

    second_response = await executor.execute(cmd)
    # After eviction, the driver MUST be re-invoked for the same command_id.
    assert driver.shake_calls == 2, "TTL-expired command_id did not allow re-execution"
    assert second_response.success is True


@pytest.mark.asyncio
async def test_distinct_command_ids_run_independently() -> None:
    """De-dup keys ONLY on command_id; distinct ids run the driver each time
    even when command + params are identical."""
    driver = _CallCountingShaker()
    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    await executor.execute(_shake_command("id_a"))
    await executor.execute(_shake_command("id_b"))

    assert driver.shake_calls == 2


@pytest.mark.asyncio
async def test_original_cancellation_propagates_to_duplicate_awaiters() -> None:
    """If the ORIGINAL caller (the one that got is_new=True from claim()) is
    cancelled mid-execution, duplicate awaiters MUST NOT hang forever. The
    executor's except BaseException arm catches CancelledError and calls
    fail() on the cache, which resolves the shared future with the
    CancelledError. Duplicate awaiters then re-raise the same exception.

    This pins the cancellation cascade contract: cancellation of the
    original execution propagates through the cache to every duplicate
    waiting on the same command_id. Without this, a gateway-resend duplicate
    that arrived while the original was still in-flight would hang on a
    permanently-pending future even though the original task has gone away.
    """
    driver = _CallCountingShaker()
    gate = asyncio.Event()
    driver.queue_gate(gate)

    cache = CommandIdempotencyCache(ttl_seconds=60.0)
    executor = _make_executor_with_stub(driver, cache)

    cmd = _shake_command("cancelled_original")

    # Kick off the first execution; it will block on the gate inside shake().
    first_task = asyncio.create_task(executor.execute(cmd))
    for _ in range(10):
        await asyncio.sleep(0)
        if driver.shake_calls == 1:
            break
    assert driver.shake_calls == 1, "first call did not enter the driver"

    # A duplicate arrives while the first is still in-flight; it gets
    # is_new=False and awaits the shared future.
    second_task = asyncio.create_task(executor.execute(cmd))
    # Yield scheduler turns so the duplicate enters its `await future`.
    for _ in range(10):
        await asyncio.sleep(0)
    assert driver.shake_calls == 1, "duplicate triggered a second driver invocation"

    # Cancel the ORIGINAL task before the gate is released. The executor's
    # except BaseException arm must call cache.fail() with the
    # CancelledError, which resolves the shared future and unblocks the
    # duplicate awaiter with the same exception.
    first_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first_task

    # The duplicate must NOT hang. It must re-raise the CancelledError that
    # the cache propagated through the shared future. A bounded wait_for
    # guards against the hang regression: if the cancellation cascade ever
    # breaks (e.g. the executor stops calling fail() on CancelledError),
    # this assertion times out loudly instead of hanging the test runner.
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second_task, timeout=1.0)

    # Driver was invoked exactly once across the whole scenario.
    assert driver.shake_calls == 1

    # After cancellation, the entry must be evicted from the cache (fail()
    # pops the entry). A NEW execution with the same command_id is therefore
    # treated as fresh and re-invokes the driver. This pins the recovery
    # path: gateway can safely retry after a cancellation cascade.
    driver_retry_gate = asyncio.Event()
    driver_retry_gate.set()
    driver.queue_gate(driver_retry_gate)
    retry_response = await executor.execute(cmd)
    assert retry_response.success is True
    assert driver.shake_calls == 2
