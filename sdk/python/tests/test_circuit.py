"""Unit tests for CircuitBreaker — the state machine, driven by a fake clock.

`time.monotonic` is monkeypatched so the cooldown transitions are deterministic and the
suite doesn't actually sleep.
"""

from __future__ import annotations

import pytest

from aegis_sdk.circuit import CircuitBreaker, State


class FakeClock:
    """Controllable stand-in for time.monotonic()."""

    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    c = FakeClock()
    monkeypatch.setattr("aegis_sdk.circuit.time.monotonic", c)
    return c


def test_starts_closed_and_allows(clock: FakeClock) -> None:
    cb = CircuitBreaker(fail_threshold=3)
    assert cb.state is State.CLOSED
    assert cb.allow() is True


def test_opens_only_at_the_threshold(clock: FakeClock) -> None:
    cb = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)
    cb.record_failure()
    cb.record_failure()
    assert cb.state is State.CLOSED  # two failures — not tripped yet
    cb.record_failure()
    assert cb.state is State.OPEN  # third trips it
    assert cb.allow() is False  # and now requests are skipped


def test_open_transitions_to_half_open_after_cooldown(clock: FakeClock) -> None:
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_s=30)
    cb.record_failure()  # → OPEN
    assert cb.allow() is False

    clock.advance(29)
    assert cb.allow() is False  # cooldown not elapsed

    clock.advance(2)  # now past 30s
    assert cb.allow() is True  # one trial request allowed
    assert cb.state is State.HALF_OPEN


def test_success_in_half_open_closes_the_breaker(clock: FakeClock) -> None:
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_s=10)
    cb.record_failure()
    clock.advance(11)
    cb.allow()  # → HALF_OPEN
    cb.record_success()
    assert cb.state is State.CLOSED


def test_failure_in_half_open_reopens_immediately(clock: FakeClock) -> None:
    cb = CircuitBreaker(fail_threshold=1, reset_timeout_s=10)
    cb.record_failure()
    clock.advance(11)
    cb.allow()  # → HALF_OPEN
    cb.record_failure()  # trial failed — straight back to OPEN
    assert cb.state is State.OPEN
    assert cb.allow() is False  # cooldown clock was reset


def test_success_resets_the_failure_count(clock: FakeClock) -> None:
    cb = CircuitBreaker(fail_threshold=3, reset_timeout_s=30)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # count back to 0
    cb.record_failure()
    cb.record_failure()
    assert cb.state is State.CLOSED  # only 2 since the reset, threshold not hit
