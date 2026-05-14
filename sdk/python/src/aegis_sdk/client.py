"""The `Aegis` facade — the one object a customer's app constructs.

    from aegis_sdk import Aegis
    aegis = Aegis(api_key="aegis_live_xxx", project="finpal-prod")
    aegis.instrument(openai_client)   # one line — every call now traced

It wires together the three SDK-A primitives and the SDK-B instrumentation:

    instrument() → RingBuffer → [background flush thread] → Transport → Aegis API

The customer's request thread only ever touches `RingBuffer.put` (lock-guarded, O(1)).
Everything network-facing — batching, retry, backoff, the circuit breaker — runs on a
daemon flush thread, so an unreachable or slow Aegis backend can never slow or break the
host app. On interpreter exit, an `atexit` hook drains whatever is still buffered.
"""

from __future__ import annotations

import atexit
import logging
import threading
from types import TracebackType
from typing import Any

from aegis_sdk.buffer import RingBuffer
from aegis_sdk.circuit import CircuitBreaker
from aegis_sdk.instrument import deinstrument as _deinstrument
from aegis_sdk.instrument import instrument as _instrument
from aegis_sdk.trace import TraceEvent
from aegis_sdk.transport import Transport

log = logging.getLogger("aegis_sdk")

# The backend's TraceBatch accepts at most 500 traces per request — never drain past it.
_MAX_BATCH = 500


class Aegis:
    """Tracing client for an LLM application.

    One instance per process. Constructing it starts the background flush thread; the
    instance can also be used as a context manager (`with Aegis(...) as aegis:`) to
    guarantee a final flush on block exit.

    `project` is a client-side label folded into every trace's metadata. The
    *authoritative* tenant (org + project) is always resolved server-side from the API
    key — the SDK can't assert its way into another tenant.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "http://localhost:8000",
        project: str | None = None,
        flush_interval_s: float = 0.5,
        batch_size: int = 100,
        buffer_size: int = 10_000,
        timeout_s: float = 2.0,
        max_attempts: int = 3,
        fail_threshold: int = 5,
        reset_timeout_s: float = 30.0,
        _transport: Transport | None = None,
        _autostart: bool = True,
    ) -> None:
        # Fail fast: a malformed key means every trace would 401 and be silently lost.
        # Better to raise at construction than to discover it never worked in prod.
        if not api_key or not api_key.startswith("aegis_"):
            raise ValueError("api_key must be a non-empty Aegis key (starts with 'aegis_').")
        if not 1 <= batch_size <= _MAX_BATCH:
            raise ValueError(f"batch_size must be between 1 and {_MAX_BATCH}")

        self._project = project
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s

        self._buffer = RingBuffer(maxlen=buffer_size)
        self._breaker = CircuitBreaker(
            fail_threshold=fail_threshold, reset_timeout_s=reset_timeout_s
        )
        self._transport = _transport or Transport(
            base_url=base_url,
            api_key=api_key,
            breaker=self._breaker,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
        )

        self._flush_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed = False
        self._last_dropped = 0
        self._thread: threading.Thread | None = None

        if _autostart:
            self._start_flush_thread()
        # Daemon thread is killed at exit without cleanup, so the atexit hook is what
        # actually guarantees the last few traces get a chance to ship.
        atexit.register(self.close)

    # -- public API -----------------------------------------------------------

    def instrument(self, client: Any, **kwargs: Any) -> Any:
        """Instrument an OpenAI-compatible client so every call is traced.

        Accepts the same keyword args as `aegis_sdk.instrument`: `provider`,
        `auto_usage`, `async_client`. Returns `client` for chaining.
        """
        return _instrument(client, sink=self._sink, **kwargs)

    def deinstrument(self, client: Any) -> bool:
        """Remove Aegis instrumentation from a client. Returns True if a patch was removed."""
        return _deinstrument(client)

    def flush(self) -> None:
        """Synchronously drain and ship everything currently buffered.

        Safe to call from any thread; serialized against the background flush thread.
        Call it before a short-lived process exits if you can't rely on `atexit`.
        """
        self._flush_once()

    def close(self) -> None:
        """Stop the flush thread, ship a final batch, release the HTTP client.

        Idempotent — safe to call explicitly *and* have it run via `atexit`.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._flush_once()  # one last drain in case the thread was mid-sleep
        self._transport.close()
        atexit.unregister(self.close)  # don't leave a dead ref in the atexit registry

    def __enter__(self) -> Aegis:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- internals ------------------------------------------------------------

    def _sink(self, event: TraceEvent) -> None:
        """The callable handed to `instrument()` — stamps the project label, then enqueues.

        `setdefault` so a per-call `aegis_metadata={"project": ...}` always wins over the
        client-wide default.
        """
        if self._project is not None:
            event.metadata.setdefault("project", self._project)
        self._buffer.put(event)

    def _start_flush_thread(self) -> None:
        self._thread = threading.Thread(
            target=self._flush_loop, name="aegis-flush", daemon=True
        )
        self._thread.start()

    def _flush_loop(self) -> None:
        # `_stop.wait` returns True when close() is called, False on timeout. Either way
        # we do one more drain after the loop so nothing buffered is left behind.
        while not self._stop.wait(timeout=self._flush_interval_s):
            try:
                self._flush_once()
            except Exception:  # noqa: BLE001 — a flush bug must not kill the thread
                log.exception("aegis: flush loop iteration failed")

    def _flush_once(self) -> None:
        """Drain the buffer in <=batch_size chunks and ship each.

        Stops early on the first failed send: during an outage we'd otherwise drain the
        whole buffer straight into a dead transport. Leaving the rest buffered lets it
        ride out the outage (bounded by the ring buffer) until the circuit recovers.
        """
        with self._flush_lock:
            self._log_drops()
            while True:
                batch = self._buffer.drain(self._batch_size)
                if not batch:
                    break
                if not self._transport.send_batch(batch):
                    break

    def _log_drops(self) -> None:
        """Surface ring-buffer overflow — a sign Aegis has been unreachable for a while."""
        dropped = self._buffer.dropped
        if dropped > self._last_dropped:
            log.warning(
                "aegis: dropped %d trace(s) to buffer overflow (Aegis may be unreachable)",
                dropped - self._last_dropped,
            )
            self._last_dropped = dropped
