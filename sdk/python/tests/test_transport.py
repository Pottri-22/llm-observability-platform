"""Unit tests for Transport — retry, status handling, and circuit interaction.

The HTTP layer is driven by `httpx.MockTransport`, so no live server is needed. Backoff
sleeps are monkeypatched out — the retry *logic* is under test, not the wall-clock wait.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from aegis_sdk.circuit import CircuitBreaker, State
from aegis_sdk.trace import TraceEvent
from aegis_sdk.transport import Transport


def _event() -> TraceEvent:
    return TraceEvent(model="gpt-4o-mini", prompt="p", completion="c")


def _make(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    breaker: CircuitBreaker | None = None,
    max_attempts: int = 3,
) -> tuple[Transport, CircuitBreaker]:
    breaker = breaker or CircuitBreaker()
    client = httpx.Client(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    transport = Transport(
        base_url="http://test",
        api_key="aegis_dev_key",
        breaker=breaker,
        max_attempts=max_attempts,
        client=client,
    )
    return transport, breaker


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aegis_sdk.transport.time.sleep", lambda _s: None)


def test_empty_batch_is_a_noop() -> None:
    transport, _ = _make(lambda _r: httpx.Response(500))
    assert transport.send_batch([]) is True  # nothing to send, nothing failed


def test_2xx_succeeds_and_resets_breaker() -> None:
    transport, breaker = _make(lambda _r: httpx.Response(201, json={"accepted": []}))
    assert transport.send_batch([_event()]) is True
    assert breaker.state is State.CLOSED


def test_5xx_is_retried_then_records_a_breaker_failure() -> None:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    transport, breaker = _make(
        handler, breaker=CircuitBreaker(fail_threshold=1), max_attempts=3
    )
    assert transport.send_batch([_event()]) is False
    assert len(calls) == 3  # exhausted every attempt
    assert breaker.state is State.OPEN  # one failure tripped the threshold-1 breaker


def test_4xx_is_not_retried_and_does_not_trip_breaker() -> None:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="invalid api key")

    transport, breaker = _make(
        handler, breaker=CircuitBreaker(fail_threshold=1), max_attempts=3
    )
    assert transport.send_batch([_event()]) is False  # batch is lost
    assert len(calls) == 1  # but not retried — the request itself is the problem
    assert breaker.state is State.CLOSED  # Aegis is up, so this is a "success" for it


def test_network_error_is_retried() -> None:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("connection refused")

    transport, breaker = _make(
        handler, breaker=CircuitBreaker(fail_threshold=1), max_attempts=2
    )
    assert transport.send_batch([_event()]) is False
    assert len(calls) == 2
    assert breaker.state is State.OPEN


def test_open_circuit_skips_the_network_entirely() -> None:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(201)

    breaker = CircuitBreaker(fail_threshold=1)
    breaker.record_failure()  # trip it OPEN
    transport, _ = _make(handler, breaker=breaker)

    assert transport.send_batch([_event()]) is False
    assert calls == []  # never touched the wire
