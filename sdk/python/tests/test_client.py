"""Unit tests for the Aegis facade.

A `FakeTransport` is injected so nothing here touches the network. Most tests run with
`_autostart=False` to keep the background thread out of the assertions; one test
explicitly exercises the autostarted flush thread.
"""

from __future__ import annotations

import time

import pytest
from _fakes import FakeClient, FakeResponse, FakeTransport, FakeUsage

from aegis_sdk.client import Aegis


def _aegis(transport: FakeTransport, **kwargs: object) -> Aegis:
    kwargs.setdefault("_autostart", False)
    return Aegis("aegis_dev_testkey", _transport=transport, **kwargs)  # type: ignore[arg-type]


def test_rejects_a_malformed_api_key() -> None:
    with pytest.raises(ValueError, match="aegis_"):
        Aegis("not-a-real-key", _transport=FakeTransport(), _autostart=False)


def test_rejects_an_out_of_range_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        _aegis(FakeTransport(), batch_size=999)


def test_instrumented_call_reaches_the_transport_on_flush() -> None:
    transport = FakeTransport()
    aegis = _aegis(transport)
    client = FakeClient(FakeResponse(content="hello", usage=FakeUsage(7, 3)))
    aegis.instrument(client)

    client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert transport.sent == []  # nothing shipped yet — it's buffered

    aegis.flush()
    assert len(transport.sent) == 1
    assert transport.sent[0].completion == "hello"


def test_project_label_is_stamped_into_metadata() -> None:
    transport = FakeTransport()
    aegis = _aegis(transport, project="finpal-prod")
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    aegis.instrument(client)

    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    aegis.flush()
    assert transport.sent[0].metadata["project"] == "finpal-prod"


def test_per_call_project_overrides_the_client_default() -> None:
    transport = FakeTransport()
    aegis = _aegis(transport, project="finpal-prod")
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    aegis.instrument(client)

    client.chat.completions.create(
        model="gpt-4o-mini", messages=[], aegis_metadata={"project": "finpal-staging"}
    )
    aegis.flush()
    assert transport.sent[0].metadata["project"] == "finpal-staging"  # setdefault — call wins


def test_flush_drains_in_batch_size_chunks() -> None:
    transport = FakeTransport()
    aegis = _aegis(transport, batch_size=2)
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    aegis.instrument(client)

    for _ in range(5):
        client.chat.completions.create(model="gpt-4o-mini", messages=[])
    aegis.flush()

    assert [len(b) for b in transport.batches] == [2, 2, 1]  # 5 events, batch_size 2
    assert len(transport.sent) == 5


def test_flush_stops_early_when_a_send_fails() -> None:
    # A failing transport must not let _flush_once drain the whole buffer into the void.
    transport = FakeTransport(succeed=False)
    aegis = _aegis(transport, batch_size=2)
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    aegis.instrument(client)

    for _ in range(6):
        client.chat.completions.create(model="gpt-4o-mini", messages=[])
    aegis.flush()

    assert len(transport.batches) == 1  # stopped after the first failed batch
    assert len(transport.batches[0]) == 2  # the rest stays buffered for the next attempt


def test_close_is_idempotent_and_closes_the_transport() -> None:
    transport = FakeTransport()
    aegis = _aegis(transport)
    aegis.close()
    aegis.close()  # second call is a no-op, not an error
    assert transport.closed is True


def test_close_does_a_final_flush() -> None:
    transport = FakeTransport()
    aegis = _aegis(transport)
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    aegis.instrument(client)

    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    aegis.close()  # never called flush() explicitly — close() must still ship it
    assert len(transport.sent) == 1


def test_context_manager_flushes_on_exit() -> None:
    transport = FakeTransport()
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))

    with Aegis("aegis_dev_testkey", _transport=transport, _autostart=False) as aegis:
        aegis.instrument(client)
        client.chat.completions.create(model="gpt-4o-mini", messages=[])

    assert len(transport.sent) == 1
    assert transport.closed is True


def test_background_thread_flushes_without_an_explicit_call() -> None:
    transport = FakeTransport()
    # _autostart=True (default) + a tight interval: the daemon thread should ship on its own.
    aegis = Aegis("aegis_dev_testkey", _transport=transport, flush_interval_s=0.02)
    try:
        client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
        aegis.instrument(client)
        client.chat.completions.create(model="gpt-4o-mini", messages=[])

        deadline = time.monotonic() + 2.0
        while not transport.sent and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(transport.sent) == 1  # flushed by the thread, no flush()/close() called
    finally:
        aegis.close()
