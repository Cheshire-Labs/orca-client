"""Idempotency cache for inbound CommandMessages.

The gateway re-sends commands with the same ``command_id`` after a
transient WebSocket drop (its reconnect grace covers the window between
disconnect and re-establishment of the session). Without de-duplication
on the client side, a side-effecting driver command could execute twice.

Two duplicate cases must be handled:

* **In-flight duplicate** -- the original execution is still running.
  Both inbound arrivals must await the same future and resolve to the
  same single response. The driver method is invoked exactly once.
* **Completed duplicate** -- the original response was already sent.
  Re-arrival of the same ``command_id`` returns the cached
  ``ResponseMessage``. The driver method is NOT invoked again.

A configurable TTL covers the gateway's reconnect grace window. After
the TTL elapses, the entry is evicted and the same ``command_id`` can
legitimately be reused. Eviction is lazy: each ``claim`` sweeps any
expired entries before deciding hit/miss.

The cache is async-safe via a single ``asyncio.Lock`` guarding the
internal mapping. The lock is released before awaiting the
``Future``, so in-flight duplicates do not serialize against each
other; they fan out from the same future independently.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from cheshire_drivers.gateway_protocol import ResponseMessage


@dataclass
class _CacheEntry:
    """One in-flight-or-completed slot keyed by command_id.

    ``future`` is set on creation; ``complete()`` resolves it and stamps
    ``completed_at`` so the lazy sweep can evict on the next access.
    """
    future: asyncio.Future[ResponseMessage]
    completed_at: Optional[float] = None


class CommandIdempotencyCache:
    """Tracks in-flight and recently-completed command_ids."""

    def __init__(
        self,
        ttl_seconds: float = 600.0,
        time_source: Optional[Callable[[], float]] = None,
    ) -> None:
        """Initialize the cache.

        Args:
            ttl_seconds: How long after completion to retain a cached
                response. Should comfortably exceed the gateway's
                reconnect grace window (typically a few minutes). The
                default of 600s (10 minutes) accommodates most
                production reconnect windows; production deployments
                that retain commands longer should raise this.
            time_source: Injectable monotonic clock for tests. Defaults
                to ``time.monotonic``.
        """
        self._ttl = ttl_seconds
        self._now = time_source if time_source is not None else time.monotonic
        self._entries: Dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def claim(
        self, command_id: str,
    ) -> Tuple[asyncio.Future[ResponseMessage], bool]:
        """Reserve a slot for ``command_id``.

        Returns:
            ``(future, is_new)``. When ``is_new`` is True the caller is
            the original executor and MUST resolve the future via
            :meth:`complete` once it has a response. When ``is_new`` is
            False the caller is a duplicate and must await ``future``
            for the original execution's response.
        """
        async with self._lock:
            self._evict_expired_locked()
            existing = self._entries.get(command_id)
            if existing is not None:
                return existing.future, False
            loop = asyncio.get_running_loop()
            future: asyncio.Future[ResponseMessage] = loop.create_future()
            self._entries[command_id] = _CacheEntry(future=future)
            return future, True

    async def is_inflight(self, command_id: str) -> bool:
        """True if ``command_id`` is claimed and still executing.

        A completed/cancelled (TTL-retained) or unknown id returns False. The
        executor uses this to tombstone a cancel only for a command that is
        actually running, so a cancel for a finished/unknown id never leaks a
        tombstone that nothing would clear.
        """
        async with self._lock:
            self._evict_expired_locked()
            entry = self._entries.get(command_id)
            return entry is not None and entry.completed_at is None

    async def complete(
        self, command_id: str, response: ResponseMessage,
    ) -> None:
        """Resolve the in-flight future for ``command_id`` and start the
        TTL eviction window. Safe to call only by the original executor
        (the caller that received ``is_new=True`` from :meth:`claim`).
        """
        async with self._lock:
            entry = self._entries.get(command_id)
            if entry is None:
                # Invariant: in-flight entries are only popped via fail();
                # complete() is called by the original executor that
                # received is_new=True from claim() and that callsite
                # holds a reference. Reaching here means either (a) a
                # test clock manipulation evicted under the lock, or
                # (b) a programming error in a future refactor. Any
                # duplicate awaiters from claim() still hold the future
                # we created earlier; leaving it unresolved would strand
                # them. Raise here so the bug is loud rather than a
                # silent hang.
                raise RuntimeError(
                    f"complete() called for unknown command_id "
                    f"{command_id!r}; in-flight entry was evicted "
                    "(test clock or refactor bug)",
                )
            if not entry.future.done():
                entry.future.set_result(response)
            entry.completed_at = self._now()

    async def fail(
        self, command_id: str, exc: BaseException,
    ) -> None:
        """Resolve the in-flight future for ``command_id`` with an
        exception. Used when the original executor's path itself raised
        and could not produce a ResponseMessage; duplicates awaiting the
        future re-raise the same exception. Entry is evicted immediately
        on fail so a retry can run cleanly.
        """
        async with self._lock:
            entry = self._entries.pop(command_id, None)
            if entry is None:
                return
            if not entry.future.done():
                entry.future.set_exception(exc)

    async def record_cancelled(
        self, command_id: str, exc: BaseException,
    ) -> None:
        """Resolve the future with a server cancellation and RETAIN the entry
        for the TTL window (unlike :meth:`fail`, which evicts immediately).

        A server cancel is terminal for that command_id: a gateway replay
        within the TTL re-raises the cancellation instead of re-invoking the
        driver, so a cancelled command never runs on reconnect.
        """
        async with self._lock:
            entry = self._entries.get(command_id)
            if entry is None:
                return
            if not entry.future.done():
                entry.future.set_exception(exc)
            # Mark the exception retrieved so a never-awaited cancelled entry
            # does not log "Future exception was never retrieved" at GC.
            entry.future.add_done_callback(lambda f: f.exception())
            entry.completed_at = self._now()

    def _evict_expired_locked(self) -> None:
        """Drop entries whose TTL has elapsed since completion. In-flight
        entries (``completed_at is None``) are never evicted. Must be
        called with ``self._lock`` held."""
        if not self._entries:
            return
        cutoff = self._now() - self._ttl
        expired = [
            cid for cid, e in self._entries.items()
            if e.completed_at is not None and e.completed_at <= cutoff
        ]
        for cid in expired:
            del self._entries[cid]
