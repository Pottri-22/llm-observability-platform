"""HTTP transport — ships batches of trace events to the Aegis ingest API.

This is the only module that touches the network. It owns three reliability behaviours,
all in service of one rule: **the customer's app must never be slowed or broken by Aegis
being unreachable.**

  1. Retry with exponential backoff — transient 5xx / network blips get up to
     `max_attempts` tries. Safe to retry blindly because every `TraceEvent` carries an
     `idempotency_key`, so the backend dedupes a batch that lands twice.
  2. Circuit breaker — once Aegis looks dead, stop trying entirely until a cooldown
     elapses (see `circuit.py`). Without this, a sustained outage turns every flush into
     a multi-second retry storm.
  3. Failure is never an exception — `send_batch` returns a bool and swallows
     everything. A raised exception here would kill the flush thread; a dropped batch
     just costs some traces.

The retry loop runs on the SDK's background flush thread, never the caller's request
thread, so the backoff `sleep`s cost the customer nothing.
"""

from __future__ import annotations

import logging
import time

import httpx

from aegis_sdk.circuit import CircuitBreaker
from aegis_sdk.trace import TraceEvent

log = logging.getLogger("aegis_sdk")

DEFAULT_TIMEOUT_S = 2.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_S = 0.2
DEFAULT_BACKOFF_CAP_S = 2.0


class Transport:
    """Sends trace batches to `POST {base_url}/v1/traces/batch`.

    The `httpx.Client` is injectable so tests can drive it with `httpx.MockTransport`
    instead of a live server. If not supplied, a real one is built with a tight
    per-attempt timeout — a slow Aegis must not become a slow customer app.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        breaker: CircuitBreaker,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        backoff_cap_s: float = DEFAULT_BACKOFF_CAP_S,
        client: httpx.Client | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._breaker = breaker
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "aegis-sdk-python",
            },
        )

    def send_batch(self, events: list[TraceEvent]) -> bool:
        """Ship one batch. Returns True iff the backend accepted it.

        Outcomes:
          * circuit OPEN              → skip the network entirely, return False
          * 2xx                      → success, breaker reset, return True
          * 4xx                      → our request is malformed (bad key, bad payload).
                                       Aegis itself is healthy, so the breaker records a
                                       *success*; we don't retry (it won't help) and
                                       return False so the loss is visible in logs.
          * 5xx / network error      → retry with backoff; if all attempts fail, record a
                                       breaker failure and return False
        """
        if not events:
            return True
        if not self._breaker.allow():
            log.warning("aegis: circuit open, dropping batch of %d traces", len(events))
            return False

        payload = {"traces": [e.to_payload() for e in events]}

        for attempt in range(self._max_attempts):
            try:
                resp = self._client.post("/v1/traces/batch", json=payload)
            except httpx.HTTPError as exc:
                # Timeout / connection error — transient, worth retrying.
                log.debug("aegis: transport error (attempt %d): %r", attempt + 1, exc)
            else:
                if resp.is_success:
                    self._breaker.record_success()
                    return True
                if 400 <= resp.status_code < 500:
                    # Client error: retrying sends the same bad request again. Aegis is
                    # up, so this is not a breaker failure — it's a config bug to fix.
                    self._breaker.record_success()
                    log.warning(
                        "aegis: backend rejected batch (%d) — %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
                # 5xx — server-side, retry.
                log.debug(
                    "aegis: backend %d (attempt %d/%d)",
                    resp.status_code,
                    attempt + 1,
                    self._max_attempts,
                )

            if attempt < self._max_attempts - 1:
                time.sleep(self._backoff_for(attempt))

        # Every attempt failed with a 5xx or a network error.
        self._breaker.record_failure()
        log.warning(
            "aegis: batch of %d traces failed after %d attempts",
            len(events),
            self._max_attempts,
        )
        return False

    def _backoff_for(self, attempt: int) -> float:
        """Exponential backoff: base, 2×base, 4×base … capped. `attempt` is 0-indexed."""
        return min(self._backoff_base_s * (2**attempt), self._backoff_cap_s)

    def close(self) -> None:
        """Release the HTTP client — only if we created it (injected clients aren't ours)."""
        if self._owns_client:
            self._client.close()
