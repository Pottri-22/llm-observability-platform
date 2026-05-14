"""Circuit breaker — stops the SDK from hammering a dead Aegis backend.

If Aegis is down, retrying every batch (3 attempts × exponential backoff) turns every
flush into a multi-second stall and a pile of doomed HTTP connections. The breaker
short-circuits that: after enough consecutive failures it `OPEN`s and the transport
skips the network entirely until a cooldown elapses, then lets a single trial request
through (`HALF_OPEN`) to probe whether Aegis is back.

States:
  CLOSED     — normal. Requests flow. Failures accrue toward the threshold.
  OPEN       — tripped. `allow()` returns False until `reset_timeout_s` passes.
  HALF_OPEN  — cooldown elapsed. Exactly one trial request is allowed through;
               its success closes the breaker, its failure re-opens it.

This is the piece that makes "the customer's app never crashes if Aegis is down" true:
a dropped trace costs nothing, a 6-second blocking retry storm costs the request.
"""

from __future__ import annotations

import threading
import time
from enum import Enum


class State(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe consecutive-failure circuit breaker.

    Shared by the transport across all flush attempts. `fail_threshold` consecutive
    failures trip it; one success anywhere resets the count. `reset_timeout_s` is how
    long it stays OPEN before allowing a HALF_OPEN trial.
    """

    def __init__(self, fail_threshold: int = 5, reset_timeout_s: float = 30.0) -> None:
        if fail_threshold < 1:
            raise ValueError("fail_threshold must be >= 1")
        self._fail_threshold = fail_threshold
        self._reset_timeout_s = reset_timeout_s
        self._lock = threading.Lock()
        self._state = State.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0

    def allow(self) -> bool:
        """Return True if a request may proceed.

        CLOSED → always. OPEN → only once the cooldown has elapsed, and in that case the
        breaker transitions to HALF_OPEN so the caller knows it's a trial. HALF_OPEN →
        True (the single trial is in flight; the result will resolve the state).
        """
        with self._lock:
            if self._state is State.OPEN:
                if time.monotonic() - self._opened_at >= self._reset_timeout_s:
                    self._state = State.HALF_OPEN
                    return True
                return False
            # CLOSED or HALF_OPEN both allow the request through.
            return True

    def record_success(self) -> None:
        """A request succeeded — clear the failure count and fully close the breaker."""
        with self._lock:
            self._consecutive_failures = 0
            self._state = State.CLOSED

    def record_failure(self) -> None:
        """A request failed — count it, and trip OPEN at the threshold.

        A failure in HALF_OPEN re-opens immediately (the trial proved Aegis is still
        down) regardless of the count, by resetting the cooldown clock.
        """
        with self._lock:
            self._consecutive_failures += 1
            if (
                self._state is State.HALF_OPEN
                or self._consecutive_failures >= self._fail_threshold
            ):
                self._state = State.OPEN
                self._opened_at = time.monotonic()

    @property
    def state(self) -> State:
        with self._lock:
            return self._state
