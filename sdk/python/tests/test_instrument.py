"""Unit tests for instrument() / deinstrument() against fake OpenAI-like clients.

No real `openai` package is needed — see `_fakes.py`. Async paths use the
`async_client=True` escape hatch and drive the coroutine with `asyncio.run`, so no
pytest async plugin is required.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from _fakes import FakeClient, FakeResponse, FakeStreamChunk, FakeToolCall, FakeUsage

from aegis_sdk.instrument import deinstrument, instrument
from aegis_sdk.trace import TraceEvent


# --- non-streaming -----------------------------------------------------------

def test_nonstream_call_emits_one_trace_and_returns_real_response() -> None:
    events: list[TraceEvent] = []
    client = FakeClient(FakeResponse(content="hello", usage=FakeUsage(7, 3)))
    instrument(client, sink=events.append)

    resp = client.chat.completions.create(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]
    )

    # The caller still gets the genuine response object, untouched.
    assert resp.choices[0].message.content == "hello"
    assert len(events) == 1
    e = events[0]
    assert e.model == "gpt-4o-mini"
    assert e.completion == "hello"
    assert e.tokens_in == 7
    assert e.tokens_out == 3
    assert e.status == "ok"
    assert e.streamed is False
    assert json.loads(e.prompt) == [{"role": "user", "content": "hi"}]


def test_instrument_is_idempotent() -> None:
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    instrument(client, sink=lambda _e: None)
    first = client.chat.completions.create
    instrument(client, sink=lambda _e: None)
    assert client.chat.completions.create is first  # not re-wrapped


def test_deinstrument_restores_the_original_create() -> None:
    # Note: `client.chat.completions.create` is a bound method — a fresh object is
    # minted on every access, so identity comparison is meaningless. We check the
    # observable behavior instead: the sentinel tag, and whether traces still emit.
    events: list[TraceEvent] = []
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    instrument(client, sink=events.append)
    assert getattr(client.chat.completions.create, "_aegis_instrumented", False) is True

    assert deinstrument(client) is True
    assert getattr(client.chat.completions.create, "_aegis_instrumented", False) is False

    # Un-instrumented again: a call now emits nothing.
    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    assert events == []

    assert deinstrument(client) is False  # already clean — nothing to remove


def test_error_call_emits_error_trace_then_reraises() -> None:
    events: list[TraceEvent] = []
    client = FakeClient(RuntimeError("boom"))
    instrument(client, sink=events.append)

    with pytest.raises(RuntimeError, match="boom"):
        client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]
        )

    assert len(events) == 1
    assert events[0].status == "error"
    assert events[0].error is not None and "boom" in events[0].error


def test_tool_call_response_is_serialized_into_completion() -> None:
    events: list[TraceEvent] = []
    tool_call = FakeToolCall(id="call_1", name="get_weather", arguments='{"city":"Pune"}')
    client = FakeClient(
        FakeResponse(content=None, tool_calls=[tool_call], usage=FakeUsage(20, 5))
    )
    instrument(client, sink=events.append)

    client.chat.completions.create(model="gpt-4o-mini", messages=[])

    # content was None; the tool call is captured instead of a blank completion.
    captured = json.loads(events[0].completion)
    assert captured[0]["name"] == "get_weather"
    assert captured[0]["arguments"] == '{"city":"Pune"}'


def test_sink_exception_never_breaks_the_caller() -> None:
    def bad_sink(_e: TraceEvent) -> None:
        raise ValueError("sink is down")

    client = FakeClient(FakeResponse(content="still-works", usage=FakeUsage(1, 1)))
    instrument(client, sink=bad_sink)

    resp = client.chat.completions.create(model="gpt-4o-mini", messages=[])
    assert resp.choices[0].message.content == "still-works"


# --- per-call metadata -------------------------------------------------------

def test_aegis_metadata_kwarg_is_popped_and_attached() -> None:
    events: list[TraceEvent] = []
    client = FakeClient(FakeResponse(content="x", usage=FakeUsage(1, 1)))
    instrument(client, sink=events.append)

    client.chat.completions.create(
        model="gpt-4o-mini", messages=[], aegis_metadata={"flow": "checkout"}
    )

    # Never forwarded to the provider...
    assert "aegis_metadata" not in client.chat.completions.received_kwargs[0]
    # ...but attached to the trace.
    assert events[0].metadata["flow"] == "checkout"


# --- model normalization -----------------------------------------------------

def test_groq_base_url_prefixes_the_model() -> None:
    events: list[TraceEvent] = []
    client = FakeClient(
        FakeResponse(content="x", usage=FakeUsage(1, 1)),
        base_url="https://api.groq.com/openai/v1",
    )
    instrument(client, sink=events.append)
    client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[])
    assert events[0].model == "groq/llama-3.3-70b-versatile"
    assert events[0].provider == "groq"


def test_already_prefixed_model_is_left_alone() -> None:
    events: list[TraceEvent] = []
    client = FakeClient(
        FakeResponse(content="x", usage=FakeUsage(1, 1)),
        base_url="https://api.groq.com/openai/v1",
    )
    instrument(client, sink=events.append)
    client.chat.completions.create(model="groq/llama-3.3-70b-versatile", messages=[])
    assert events[0].model == "groq/llama-3.3-70b-versatile"  # no double prefix


def test_openai_model_stays_bare() -> None:
    events: list[TraceEvent] = []
    client = FakeClient(
        FakeResponse(content="x", usage=FakeUsage(1, 1)),
        base_url="https://api.openai.com/v1",
    )
    instrument(client, sink=events.append)
    client.chat.completions.create(model="gpt-4o-mini", messages=[])
    assert events[0].model == "gpt-4o-mini"  # OpenAI catalog keys are bare


# --- streaming ---------------------------------------------------------------

def test_auto_usage_injects_stream_options() -> None:
    client = FakeClient([FakeStreamChunk("hi"), FakeStreamChunk(usage=FakeUsage(5, 1), has_choice=False)])
    instrument(client, sink=lambda _e: None)
    list(client.chat.completions.create(model="gpt-4o-mini", messages=[], stream=True))
    assert client.chat.completions.received_kwargs[0]["stream_options"] == {"include_usage": True}


def test_auto_usage_false_does_not_inject() -> None:
    client = FakeClient([FakeStreamChunk("hi")])
    instrument(client, sink=lambda _e: None, auto_usage=False)
    list(client.chat.completions.create(model="gpt-4o-mini", messages=[], stream=True))
    assert "stream_options" not in client.chat.completions.received_kwargs[0]


def test_stream_passes_every_chunk_through_and_emits_on_completion() -> None:
    events: list[TraceEvent] = []
    chunks = [
        FakeStreamChunk("Hel"),
        FakeStreamChunk("lo"),
        FakeStreamChunk(usage=FakeUsage(4, 2), has_choice=False),
    ]
    client = FakeClient(chunks)
    instrument(client, sink=events.append)

    stream = client.chat.completions.create(model="gpt-4o-mini", messages=[], stream=True)
    received = list(stream)

    assert len(received) == 3  # caller still sees every chunk
    assert len(events) == 1  # ...and exactly one trace, after the stream finished
    e = events[0]
    assert e.completion == "Hello"
    assert e.tokens_in == 4
    assert e.tokens_out == 2
    assert e.streamed is True


# --- async (escape hatch) ----------------------------------------------------

def test_async_client_nonstream() -> None:
    events: list[TraceEvent] = []
    client = FakeClient(
        FakeResponse(content="async-hello", usage=FakeUsage(8, 4)), is_async=True
    )
    instrument(client, sink=events.append, async_client=True)

    async def run() -> object:
        return await client.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}]
        )

    resp = asyncio.run(run())

    assert resp.choices[0].message.content == "async-hello"
    assert len(events) == 1
    assert events[0].completion == "async-hello"
    assert events[0].tokens_in == 8


def test_async_client_streaming() -> None:
    events: list[TraceEvent] = []
    chunks = [
        FakeStreamChunk("a"),
        FakeStreamChunk("b"),
        FakeStreamChunk(usage=FakeUsage(3, 2), has_choice=False),
    ]
    client = FakeClient(chunks, is_async=True)
    instrument(client, sink=events.append, async_client=True)

    async def run() -> list[object]:
        stream = await client.chat.completions.create(
            model="gpt-4o-mini", messages=[], stream=True
        )
        return [chunk async for chunk in stream]

    received = asyncio.run(run())

    assert len(received) == 3
    assert len(events) == 1
    assert events[0].completion == "ab"
    assert events[0].streamed is True
