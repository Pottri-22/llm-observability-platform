"""In-memory ring buffer for pending trace events.

The SDK never blocks the customer's request thread on a network call. Instrumentation
wrappers `put()` an event and return immediately; a background flush thread `drain()`s
batches and ships them. This module is just the queue between those two sides.

Why a *ring* buffer (bounded, drop-oldest) and not an unbounded queue:
  If Aegis is unreachable for an hour, an unbounded queue grows until the customer's
  process OOMs — Aegis being down would take the customer's app down with it, which is
  the exact opposite of what an observability SDK should do. A bounded buffer means the
  worst case is "we lose the oldest traces", never "we crash the host app". Newest
  traces are kept because they're the ones most likely still relevant when Aegis
  recovers. `dropped` is counted so the flush thread can surface the loss.
"""

from __future__ import annotations

import threading
from collections import deque

from aegis_sdk.trace import TraceEvent

DEFAULT_MAXLEN = 10_000


class RingBuffer:
    """Thread-safe bounded buffer of `TraceEvent`s. Drops oldest on overflow.

    Every caller — N instrumented call sites across the app's threads on one side, the
    single flush thread on the other — goes through one lock. The critical sections are
    O(1) (`append`) or O(batch) (`drain`), so contention is negligible next to the LLM
    calls themselves.
    """

    def __init__(self, maxlen: int = DEFAULT_MAXLEN) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self._maxlen = maxlen
        self._dq: deque[TraceEvent] = deque()
        self._lock = threading.Lock()
        self._dropped = 0

    def put(self, event: TraceEvent) -> None:
        """Enqueue an event. If the buffer is full, drop the oldest and count it.

        We don't lean on `deque(maxlen=...)` auto-eviction because that drops silently —
        we want an explicit `_dropped` tally so the flush thread can log "lost N traces,
        Aegis may be down".
        """
        with self._lock:
            if len(self._dq) >= self._maxlen:
                self._dq.popleft()
                self._dropped += 1
            self._dq.append(event)

    def drain(self, max_n: int) -> list[TraceEvent]:
        """Remove and return up to `max_n` events, oldest first.

        Returns an empty list if the buffer is empty. The flush thread calls this with
        the batch-size cap so one HTTP POST never exceeds the backend's 500-trace limit.
        """
        if max_n < 1:
            raise ValueError("max_n must be >= 1")
        with self._lock:
            n = min(max_n, len(self._dq))
            return [self._dq.popleft() for _ in range(n)]

    def __len__(self) -> int:
        with self._lock:
            return len(self._dq)

    @property
    def dropped(self) -> int:
        """Total events dropped to overflow since construction (monotonic)."""
        with self._lock:
            return self._dropped
