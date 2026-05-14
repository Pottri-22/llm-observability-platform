"""Unit tests for TraceEvent — construction defaults and the wire-shape mapping."""

from __future__ import annotations

import json

from aegis_sdk.trace import TraceEvent


def test_idempotency_key_defaults_to_trace_id() -> None:
    e = TraceEvent(model="m", prompt="p", completion="c")
    assert e.trace_id  # a UUID4 was minted
    assert e.idempotency_key == e.trace_id


def test_explicit_idempotency_key_is_preserved() -> None:
    e = TraceEvent(model="m", prompt="p", completion="c", idempotency_key="k-1")
    assert e.idempotency_key == "k-1"
    assert e.trace_id != "k-1"


def test_from_call_serializes_messages_into_prompt() -> None:
    msgs = [{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}]
    e = TraceEvent.from_call(
        model="gpt-4o-mini",
        messages=msgs,
        completion="hello",
        tokens_in=5,
        tokens_out=2,
        latency_ms=120.7,
        status="ok",
        streamed=False,
    )
    # The whole conversation is persisted, not just the last turn.
    assert json.loads(e.prompt) == msgs
    assert e.latency_ms == 120  # float latency coerced to int


def test_from_call_coerces_none_tokens_to_zero() -> None:
    # Streaming calls without include_usage report None tokens — must not reach the wire.
    e = TraceEvent.from_call(
        model="m",
        messages=[],
        completion="",
        tokens_in=None,
        tokens_out=None,
        latency_ms=0,
        status="ok",
        streamed=True,
    )
    assert e.tokens_in == 0
    assert e.tokens_out == 0


def test_to_payload_maps_to_backend_schema() -> None:
    e = TraceEvent.from_call(
        model="groq/llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": "hi"}],
        completion="hello",
        tokens_in=12,
        tokens_out=4,
        latency_ms=90,
        status="error",
        streamed=True,
        provider="groq",
        error="ReadTimeout",
        user_metadata={"flow": "demo"},
    )
    p = e.to_payload()

    assert p["model"] == "groq/llama-3.3-70b-versatile"
    assert p["tokens_in"] == 12
    assert p["idempotency_key"] == e.trace_id
    # cost_usd is never sent — the server is the source of truth.
    assert "cost_usd" not in p
    # user metadata is preserved alongside the namespaced aegis block.
    assert p["metadata"]["flow"] == "demo"
    assert p["metadata"]["aegis"]["status"] == "error"
    assert p["metadata"]["aegis"]["streamed"] is True
    assert p["metadata"]["aegis"]["provider"] == "groq"
    assert p["metadata"]["aegis"]["error"] == "ReadTimeout"


def test_to_payload_omits_error_key_when_clean() -> None:
    e = TraceEvent(model="m", prompt="p", completion="c")
    assert "error" not in e.to_payload()["metadata"]["aegis"]
