"""Instrumentation — monkey-patches an OpenAI-compatible client so every LLM call
emits a `TraceEvent` to a sink, with zero changes to the caller's call sites.

This is the package version of the Week 0 Block 3 prototype (`week0/instrument_demo.py`),
with the six follow-ups that block's notes flagged for v0.1 now folded in:

  1. `auto_usage`     — streaming calls get `stream_options={"include_usage": True}`
                        injected so the final chunk carries token counts.
  2. `async_client`   — explicit override for async clients that aren't an
                        `openai.AsyncOpenAI` subclass (see §2.3 of NOTES_block3.md —
                        `inspect.iscoroutinefunction` lies, so we check the client type).
  3. full messages    — the whole `messages` list is persisted (via `TraceEvent.from_call`),
                        not just the last turn.
  4. `deinstrument`   — the original `create` is stashed on the wrapper so the patch
                        can be cleanly removed (tests, eval baselines).
  5. tool_calls       — when the model returns a tool call instead of text, the call is
                        serialized into `completion` rather than recorded as blank.
  6. model normalize  — the model id is normalized to the backend cost-catalog
                        convention (`groq/` prefix) so server-side cost lookup hits.

The sink is injected, not hard-coded: the `Aegis` facade (SDK-C) passes its ring
buffer's `put`. That keeps this module free of any transport/threading concerns — it
only captures and hands off.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

from aegis_sdk.trace import TraceEvent

log = logging.getLogger("aegis_sdk")

Sink = Callable[[TraceEvent], None]

# Tags placed on the wrapped `create` so a second instrument() call is a no-op and
# deinstrument() can recover the original. Stored on the function object itself.
_SENTINEL = "_aegis_instrumented"
_ORIGINAL = "_aegis_original"

# Providers whose models the cost catalog keys with a `provider/` prefix. OpenAI and
# Anthropic models are keyed bare, so only this set triggers prefixing.
_PREFIXED_PROVIDERS = {"groq"}


# ---------------------------------------------------------------------------
# Client-shape + identity helpers
# ---------------------------------------------------------------------------

def _is_async_client(client: Any, override: bool | None) -> bool:
    """Decide whether `client` needs the async wrapper.

    An explicit `override` always wins (the escape hatch for custom async clients).
    Otherwise we check `isinstance(client, AsyncAPIClient)` — NOT
    `inspect.iscoroutinefunction`, which the openai SDK's method wrapping defeats
    (NOTES_block3.md §2.3). If openai isn't importable, default to sync.
    """
    if override is not None:
        return override
    try:
        from openai._base_client import AsyncAPIClient  # not in openai's public API
    except Exception:  # noqa: BLE001 — openai optional / internal path moved
        return False
    return isinstance(client, AsyncAPIClient)


def _base_url_of(client: Any) -> str | None:
    """Best-effort read of the client's base URL — used to infer the provider."""
    try:
        return str(client.base_url)
    except Exception:  # noqa: BLE001
        return None


def _resolve_provider(provider: str | None, base_url: str | None) -> str | None:
    """Resolve the provider: explicit arg wins, else infer from the base URL host."""
    if provider:
        return provider
    if not base_url:
        return None
    if "groq.com" in base_url:
        return "groq"
    if "anthropic.com" in base_url:
        return "anthropic"
    if "openai.com" in base_url:
        return "openai"
    return None


def _normalize_model(model: str, provider: str | None) -> str:
    """Map a raw model id onto the backend cost-catalog key convention.

    Already-prefixed ids (anything containing `/`, e.g. a LiteLLM-style
    `groq/llama-3.3-70b-versatile`) pass through untouched. A bare id from a
    prefix-using provider gets the prefix added; everything else stays bare.
    """
    if not model or "/" in model:
        return model
    if provider in _PREFIXED_PROVIDERS:
        return f"{provider}/{model}"
    return model


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------

def _tool_call_to_dict(tc: Any) -> dict[str, Any]:
    """Flatten one tool-call object into a plain dict for JSON serialization."""
    fn = getattr(tc, "function", None)
    return {
        "id": getattr(tc, "id", None),
        "type": getattr(tc, "type", "function"),
        "name": getattr(fn, "name", None),
        "arguments": getattr(fn, "arguments", None),
    }


def _extract_completion(result: Any) -> str:
    """Pull the assistant's output text from a non-streaming response.

    When the model returns a tool call, `message.content` is None — Block 3's prototype
    recorded that as a blank completion. Here we serialize the tool calls instead, so a
    tool-using turn shows *something* in the trace detail view rather than an empty box.
    """
    try:
        message = result.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    if getattr(message, "content", None):
        return message.content
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        return json.dumps([_tool_call_to_dict(tc) for tc in tool_calls], default=str)
    return ""


# ---------------------------------------------------------------------------
# Streaming pass-through wrappers
#
# Both are generators that yield every chunk straight through to the caller while
# accumulating trace state on the side — the caller iterates them exactly as it would
# the raw SDK stream. The `finally` fires `on_done` even on early break or mid-stream
# error (partial read), so a trace is always emitted. See NOTES_block3.md §2.1.
# ---------------------------------------------------------------------------

OnDone = Callable[[str, "int | None", "int | None", "str | None"], None]


def _wrap_stream_sync(stream: Any, on_done: OnDone) -> Iterator[Any]:
    parts: list[str] = []
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    try:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
            if getattr(chunk, "usage", None) is not None:
                tokens_in = chunk.usage.prompt_tokens
                tokens_out = chunk.usage.completion_tokens
            yield chunk
    except Exception as exc:  # noqa: BLE001 — recorded then re-raised below
        error = repr(exc)
        raise
    finally:
        on_done("".join(parts), tokens_in, tokens_out, error)


async def _wrap_stream_async(stream: Any, on_done: OnDone) -> Any:
    parts: list[str] = []
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    try:
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
            if getattr(chunk, "usage", None) is not None:
                tokens_in = chunk.usage.prompt_tokens
                tokens_out = chunk.usage.completion_tokens
            yield chunk
    except Exception as exc:  # noqa: BLE001 — recorded then re-raised below
        error = repr(exc)
        raise
    finally:
        on_done("".join(parts), tokens_in, tokens_out, error)


# ---------------------------------------------------------------------------
# instrument / deinstrument
# ---------------------------------------------------------------------------

def instrument(
    client: Any,
    *,
    sink: Sink,
    provider: str | None = None,
    auto_usage: bool = True,
    async_client: bool | None = None,
) -> Any:
    """Patch `client.chat.completions.create` to emit a `TraceEvent` per call.

    Patches the *instance*, not the class — a second bare `OpenAI()` in the same process
    is unaffected (eval baselines must not start emitting traces). Calling `instrument`
    twice on the same client is a no-op. Returns `client` so the call can be chained.

    A per-call `aegis_metadata={...}` kwarg is recognized: it is popped before the
    request reaches the provider and attached to that trace's metadata.
    """
    completions = client.chat.completions
    original = completions.create
    if getattr(original, _SENTINEL, False):
        return client  # already instrumented — idempotent

    is_async = _is_async_client(client, async_client)
    resolved_provider = _resolve_provider(provider, _base_url_of(client))

    def _emit(
        *,
        messages: list[dict[str, Any]],
        model: str,
        completion: str,
        tokens_in: int | None,
        tokens_out: int | None,
        started: float,
        status: str,
        streamed: bool,
        error: str | None,
        user_metadata: dict[str, Any] | None,
    ) -> None:
        # The sink (ring buffer) must never break the caller's call — swallow anything.
        try:
            sink(
                TraceEvent.from_call(
                    model=model,
                    messages=messages,
                    completion=completion,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    status=status,
                    streamed=streamed,
                    provider=resolved_provider,
                    error=error,
                    user_metadata=user_metadata,
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("aegis: failed to emit trace (call itself is unaffected)")

    def _prepare(kwargs: dict[str, Any]) -> tuple[
        list[dict[str, Any]], str, bool, dict[str, Any] | None
    ]:
        """Read call args, normalize the model, inject stream_options, pop aegis_metadata.

        Mutates `kwargs` in place: removes our private `aegis_metadata` (so it never
        reaches the provider) and, when `auto_usage` is on, ensures streaming calls ask
        for the usage chunk.
        """
        user_metadata = kwargs.pop("aegis_metadata", None)
        messages = list(kwargs.get("messages") or [])
        model = _normalize_model(kwargs.get("model", ""), resolved_provider)
        streamed = bool(kwargs.get("stream"))
        if streamed and auto_usage:
            opts = dict(kwargs.get("stream_options") or {})
            opts.setdefault("include_usage", True)
            kwargs["stream_options"] = opts
        return messages, model, streamed, user_metadata

    if is_async:

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            messages, model, streamed, user_metadata = _prepare(kwargs)
            started = time.perf_counter()
            try:
                result = await original(*args, **kwargs)
            except Exception as exc:
                _emit(
                    messages=messages, model=model, completion="",
                    tokens_in=None, tokens_out=None, started=started,
                    status="error", streamed=streamed, error=repr(exc),
                    user_metadata=user_metadata,
                )
                raise
            if streamed:
                def on_done(text: str, tin: int | None, tout: int | None, err: str | None) -> None:
                    _emit(
                        messages=messages, model=model, completion=text,
                        tokens_in=tin, tokens_out=tout, started=started,
                        status="error" if err else "ok", streamed=True, error=err,
                        user_metadata=user_metadata,
                    )
                return _wrap_stream_async(result, on_done)
            usage = getattr(result, "usage", None)
            _emit(
                messages=messages, model=model, completion=_extract_completion(result),
                tokens_in=getattr(usage, "prompt_tokens", None),
                tokens_out=getattr(usage, "completion_tokens", None),
                started=started, status="ok", streamed=False, error=None,
                user_metadata=user_metadata,
            )
            return result

    else:

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            messages, model, streamed, user_metadata = _prepare(kwargs)
            started = time.perf_counter()
            try:
                result = original(*args, **kwargs)
            except Exception as exc:
                _emit(
                    messages=messages, model=model, completion="",
                    tokens_in=None, tokens_out=None, started=started,
                    status="error", streamed=streamed, error=repr(exc),
                    user_metadata=user_metadata,
                )
                raise
            if streamed:
                def on_done(text: str, tin: int | None, tout: int | None, err: str | None) -> None:
                    _emit(
                        messages=messages, model=model, completion=text,
                        tokens_in=tin, tokens_out=tout, started=started,
                        status="error" if err else "ok", streamed=True, error=err,
                        user_metadata=user_metadata,
                    )
                return _wrap_stream_sync(result, on_done)
            usage = getattr(result, "usage", None)
            _emit(
                messages=messages, model=model, completion=_extract_completion(result),
                tokens_in=getattr(usage, "prompt_tokens", None),
                tokens_out=getattr(usage, "completion_tokens", None),
                started=started, status="ok", streamed=False, error=None,
                user_metadata=user_metadata,
            )
            return result

    setattr(wrapped, _SENTINEL, True)
    setattr(wrapped, _ORIGINAL, original)
    completions.create = wrapped
    return client


def deinstrument(client: Any) -> bool:
    """Remove the patch from `client`, restoring the original `create`.

    Returns True if a patch was removed, False if the client wasn't instrumented.
    Used by tests and by callers that want a clean client back (e.g. eval baselines).
    """
    completions = client.chat.completions
    current = completions.create
    original = getattr(current, _ORIGINAL, None)
    if original is None:
        return False
    completions.create = original
    return True
