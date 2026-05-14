"""Unit tests for RingBuffer — FIFO drain, bounded overflow, thread-safety."""

from __future__ import annotations

import threading

import pytest

from aegis_sdk.buffer import RingBuffer
from aegis_sdk.trace import TraceEvent


def _event() -> TraceEvent:
    return TraceEvent(model="gpt-4o-mini", prompt="p", completion="c")


def test_put_and_drain_are_fifo() -> None:
    buf = RingBuffer(maxlen=10)
    events = [_event() for _ in range(3)]
    for e in events:
        buf.put(e)
    assert len(buf) == 3
    assert buf.drain(10) == events  # oldest first
    assert len(buf) == 0


def test_drain_respects_max_n() -> None:
    buf = RingBuffer(maxlen=10)
    for _ in range(5):
        buf.put(_event())
    assert len(buf.drain(2)) == 2
    assert len(buf) == 3


def test_drain_on_empty_returns_empty_list() -> None:
    assert RingBuffer().drain(5) == []


def test_overflow_drops_oldest_and_counts_them() -> None:
    buf = RingBuffer(maxlen=3)
    events = [_event() for _ in range(5)]
    for e in events:
        buf.put(e)
    assert len(buf) == 3
    assert buf.dropped == 2
    # The two oldest were evicted; the three newest survive, still in order.
    assert buf.drain(10) == events[2:]


def test_concurrent_puts_lose_nothing() -> None:
    buf = RingBuffer(maxlen=10_000)

    def worker() -> None:
        for _ in range(1_000):
            buf.put(_event())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(buf) == 8_000
    assert buf.dropped == 0


def test_maxlen_must_be_positive() -> None:
    with pytest.raises(ValueError):
        RingBuffer(maxlen=0)
