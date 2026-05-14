"""Week 0 · Block 3 — `instrument_openai(client)` prototype.

The shipping SDK in v0.1 will be `aegis.instrument(client)`. This file is
the standalone walkthrough that proves the pattern before any v0.1 code is
written.

Covers:
  1. sync clients         (openai.OpenAI)
  2. async clients        (openai.AsyncOpenAI)
  3. stream=True calls    (sync + async) — wrapper yields chunks through,
                          emits trace after final chunk
  4. idempotency          — calling instrument_openai() twice is a no-op

The trace sink is print()-as-JSON. v0.1's SDK replaces _emit() with an
HTTP POST to /v1/traces.

Pairs with: NOTES_block3.md (gotchas + v0.1 decisions from this session).

Run:
    week0\\.venv\\Scripts\\python.exe week0\\instrument_demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAI
from openai._base_client import AsyncAPIClient  # not exported from public __init__

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
from app.services.cost import compute_cost_usd  # noqa: E402  # type: ignore[import]

load_dotenv(REPO / ".env")

GROQ_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_KEY or not GROQ_KEY.startswith("gsk_"):
    sys.exit("GROQ_API_KEY missing or malformed in .env (expected `gsk_...`).")

GROQ_BASE = "https://api.groq.com/openai/v1"
MODEL = "llama-3.3-70b-versatile"
COST_KEY = f"groq/{MODEL}"

# Sentinel: if the method on client.chat.completions already carries this,
# the client was already instrumented — skip to preserve idempotency.
_SENTINEL = "_aegis_instrumented"


# ---------------------------------------------------------------------------
# Sink
# ---------------------------------------------------------------------------

def _emit(event: dict[str, Any]) -> None:
    """Trace sink. v0.1 swaps this for HTTP POST to /v1/traces."""
    print("  trace>", json.dumps(event, default=str))


# ---------------------------------------------------------------------------
# Event builder
# ---------------------------------------------------------------------------

def _build_event(
    *,
    model: str,
    prompt: str,
    response: str,
    tokens_in: int | None,
    tokens_out: int | None,
    latency_ms: float,
    status: str,
    streamed: bool,
) -> dict[str, Any]:
    cost = (
        compute_cost_usd(COST_KEY, tokens_in, tokens_out)
        if tokens_in is not None and tokens_out is not None
        else None
    )
    return {
        "model": model,
        "prompt_preview": prompt[:80],
        "response_preview": response[:80],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "latency_ms": round(latency_ms, 1),
        "status": status,
        "streamed": streamed,
    }


# ---------------------------------------------------------------------------
# Stream pass-through wrappers
#
# The user iterates these exactly as they would iterate the raw SDK stream.
# We accumulate state on the way through and fire _on_done when exhausted.
#
# WHY a generator (not buffer-then-return): returning a list breaks any
# caller that checks isinstance(resp, Stream) or depends on chunk-by-chunk
# delivery for live UX. The pass-through generator is invisible to the caller.
# ---------------------------------------------------------------------------

def _wrap_stream_sync(stream: Any, on_done: Callable[[str, int | None, int | None], None]) -> Any:
    text_parts: list[str] = []
    tokens_in: int | None = None
    tokens_out: int | None = None
    try:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text_parts.append(chunk.choices[0].delta.content)
            if getattr(chunk, "usage", None) is not None:
                tokens_in = chunk.usage.prompt_tokens
                tokens_out = chunk.usage.completion_tokens
            yield chunk
    finally:
        # finally fires even if the caller breaks out early (partial read).
        on_done("".join(text_parts), tokens_in, tokens_out)


async def _wrap_stream_async(stream: Any, on_done: Callable[[str, int | None, int | None], None]) -> Any:
    text_parts: list[str] = []
    tokens_in: int | None = None
    tokens_out: int | None = None
    try:
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                text_parts.append(chunk.choices[0].delta.content)
            if getattr(chunk, "usage", None) is not None:
                tokens_in = chunk.usage.prompt_tokens
                tokens_out = chunk.usage.completion_tokens
            yield chunk
    finally:
        on_done("".join(text_parts), tokens_in, tokens_out)


# ---------------------------------------------------------------------------
# Core: instrument_openai
# ---------------------------------------------------------------------------

def instrument_openai(client: Any) -> Any:
    """Monkey-patch client.chat.completions.create to emit a trace per call.

    Patches the *instance*, not the class — other OpenAI() clients in this
    process are unaffected. Calling this twice on the same client is a no-op.

    Works on both OpenAI (sync) and AsyncOpenAI (async): detects which by
    checking isinstance(client, AsyncAPIClient). inspect.iscoroutinefunction
    is unreliable here — openai SDK wraps methods in a way that strips the
    CO_COROUTINE flag, so iscoroutinefunction returns False even for async
    methods (confirmed at runtime; see NOTES_block3.md §2.3).

    Returns client so you can chain:
        client = instrument_openai(OpenAI(...))
    """
    completions = client.chat.completions
    original = completions.create

    if getattr(original, _SENTINEL, False):
        return client  # already wrapped

    is_async = isinstance(client, AsyncAPIClient)

    def _extract_prompt(kwargs: dict[str, Any]) -> str:
        # v0.1: keep full messages list in trace; preview is enough for demo.
        msgs = kwargs.get("messages") or []
        return msgs[-1].get("content", "") if msgs else ""

    def _make_on_done(prompt: str, model: str, started: float, streamed: bool) -> Callable:
        def on_done(response_text: str, tin: int | None, tout: int | None) -> None:
            _emit(_build_event(
                model=model,
                prompt=prompt,
                response=response_text,
                tokens_in=tin,
                tokens_out=tout,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                status="ok",
                streamed=streamed,
            ))
        return on_done

    if is_async:
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            prompt = _extract_prompt(kwargs)
            model = kwargs.get("model", "")
            streamed = bool(kwargs.get("stream"))
            started = time.perf_counter()
            try:
                result = await original(*args, **kwargs)
            except Exception as exc:
                _emit(_build_event(
                    model=model, prompt=prompt, response=f"<error: {exc!r}>",
                    tokens_in=None, tokens_out=None,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status="error", streamed=streamed,
                ))
                raise
            if streamed:
                # Return async generator; caller does `async for chunk in result`.
                return _wrap_stream_async(result, _make_on_done(prompt, model, started, True))
            usage = getattr(result, "usage", None)
            content = result.choices[0].message.content or ""
            _emit(_build_event(
                model=model, prompt=prompt, response=content,
                tokens_in=getattr(usage, "prompt_tokens", None),
                tokens_out=getattr(usage, "completion_tokens", None),
                latency_ms=(time.perf_counter() - started) * 1000.0,
                status="ok", streamed=False,
            ))
            return result
    else:
        def wrapped(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
            prompt = _extract_prompt(kwargs)
            model = kwargs.get("model", "")
            streamed = bool(kwargs.get("stream"))
            started = time.perf_counter()
            try:
                result = original(*args, **kwargs)
            except Exception as exc:
                _emit(_build_event(
                    model=model, prompt=prompt, response=f"<error: {exc!r}>",
                    tokens_in=None, tokens_out=None,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status="error", streamed=streamed,
                ))
                raise
            if streamed:
                return _wrap_stream_sync(result, _make_on_done(prompt, model, started, True))
            usage = getattr(result, "usage", None)
            content = result.choices[0].message.content or ""
            _emit(_build_event(
                model=model, prompt=prompt, response=content,
                tokens_in=getattr(usage, "prompt_tokens", None),
                tokens_out=getattr(usage, "completion_tokens", None),
                latency_ms=(time.perf_counter() - started) * 1000.0,
                status="ok", streamed=False,
            ))
            return result

    setattr(wrapped, _SENTINEL, True)
    completions.create = wrapped
    return client


# ---------------------------------------------------------------------------
# Demo: call sites are IDENTICAL to vanilla OpenAI — only the instrument_openai
# call above each client construction is new.
# ---------------------------------------------------------------------------

PROMPT = "In one sentence, what is the difference between TCP and UDP?"


def demo_sync_nonstream(client: OpenAI) -> None:
    print("\n[1/4] sync, stream=False")
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT}],
    )
    print(f"      {resp.choices[0].message.content[:100]}")


def demo_sync_stream(client: OpenAI) -> None:
    print("\n[2/4] sync, stream=True")
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
        stream_options={"include_usage": True},
    )
    parts: list[str] = []
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            parts.append(chunk.choices[0].delta.content)
    print(f"      {''.join(parts)[:100]}")


async def demo_async_nonstream(client: AsyncOpenAI) -> None:
    print("\n[3/4] async, stream=False")
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT}],
    )
    print(f"      {resp.choices[0].message.content[:100]}")


async def demo_async_stream(client: AsyncOpenAI) -> None:
    print("\n[4/4] async, stream=True")
    stream = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
        stream_options={"include_usage": True},
    )
    parts: list[str] = []
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            parts.append(chunk.choices[0].delta.content)
    print(f"      {''.join(parts)[:100]}")


def demo_idempotency(client: OpenAI) -> None:
    print("\n[idempotency]")
    fn_before = client.chat.completions.create
    instrument_openai(client)
    fn_after = client.chat.completions.create
    assert fn_before is fn_after, "FAIL: second instrument_openai() re-wrapped the client"
    print(f"      OK — same function object after second call (id={id(fn_before)})")


async def _async_demos() -> None:
    aclient = instrument_openai(AsyncOpenAI(api_key=GROQ_KEY, base_url=GROQ_BASE))
    await demo_async_nonstream(aclient)
    await demo_async_stream(aclient)


def main() -> int:
    sclient = instrument_openai(OpenAI(api_key=GROQ_KEY, base_url=GROQ_BASE))
    demo_sync_nonstream(sclient)
    demo_sync_stream(sclient)
    asyncio.run(_async_demos())
    demo_idempotency(sclient)
    print("\nDone — 4 live traces emitted, 0 call sites modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
