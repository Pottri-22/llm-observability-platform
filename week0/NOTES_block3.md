# Week 0 · Block 3 — OpenAI SDK instrumentation pattern

**As of:** 2026-05-11 (Sun) · pre-Week 1 · Block 3 complete  
**Pairs with:** [instrument_demo.py](instrument_demo.py), [litellm_notes.md](litellm_notes.md)  
**Reading goal:** so v0.1 SDK's `aegis.instrument(client)` doesn't re-learn any of these.

---

## 1. What was built

`instrument_openai(client)` — a function that monkey-patches
`client.chat.completions.create` on a single client *instance* so every
subsequent call automatically captures:

```
model · prompt_preview · response_preview · tokens_in · tokens_out
cost_usd · latency_ms · status · streamed
```

and emits to a sink (print → HTTP POST in v0.1). The demo proves all four
paths without touching a single call site:

| Path | Result |
|---|---|
| sync, stream=False | trace emitted immediately after response |
| sync, stream=True | trace emitted after final chunk, chunks pass through |
| async, stream=False | same as sync; `await` handled by `async def wrapped` |
| async, stream=True | async generator passes chunks through |
| idempotency | second `instrument_openai()` on same client is a no-op |

---

## 2. The two failure modes a naive first attempt would hit

### 2.1 Consuming the stream inside the wrapper (silent data loss)

**What the naive version does:**

```python
# WRONG — consumes the stream internally, then returns the raw SDK object
def wrapped(*args, **kwargs):
    started = time.perf_counter()
    result = original(*args, **kwargs)
    if kwargs.get("stream"):
        text = ""
        for chunk in result:          # ← iterator is now exhausted
            text += chunk.choices[0].delta.content or ""
        _emit(...)                    # trace emitted here (good)
    return result                     # ← caller gets an exhausted iterator
```

**What breaks:** the caller does `for chunk in stream: ...` and gets zero
iterations. No error is raised — the loop just exits immediately. The
response appears to be empty. This is the hardest bug to diagnose because
the trace looks fine (the wrapper saw all the data), but the application
silently produces blank output.

**The fix:** never consume the iterator; return a generator that *yields
through* while accumulating:

```python
def _wrap_stream_sync(stream, on_done):
    parts = []
    try:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
            yield chunk          # ← caller still gets every chunk
    finally:
        on_done("".join(parts), ...)   # ← fires when iterator exhausts
```

The `finally` block guarantees `on_done` fires even if the caller breaks
out of the loop early (partial read for timeout/cancellation). An `except`
block alone would miss that case.

---

### 2.2 Ignoring sync vs async — one `def` wrapper for both client types

**What the naive version does:**

```python
# WRONG — works fine for OpenAI() but silently corrupts AsyncOpenAI()
def wrapped(*args, **kwargs):
    started = time.perf_counter()
    result = original(*args, **kwargs)   # ← for async client, result is a coroutine
    ...
    return result
```

**What breaks — two distinct symptoms:**

1. `result.choices[0].message.content` raises `AttributeError: 'coroutine'
   object has no attribute 'choices'` because `original()` on an
   `AsyncOpenAI` client returns an unawaited coroutine, not a response.
   The latency is also ≈0 ms (measured before the network call runs).

2. Python 3.12 emits `RuntimeWarning: coroutine 'AsyncCompletions.create'
   was never awaited` at GC time, which is baffling if you didn't expect
   your wrapper to suppress the await.

**The fix:** choose the right function *shape* at instrument time, before
any call happens:

```python
is_async = isinstance(client, AsyncAPIClient)  # see §2.3 — iscoroutinefunction fails here

if is_async:
    async def wrapped(*args, **kwargs):
        result = await original(*args, **kwargs)
        ...
else:
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        ...
```

You can't detect it at call time because by then you are already inside a
`def` that cannot `await`.

### 2.3 `inspect.iscoroutinefunction` lies for openai SDK methods

This is the **real** failure mode 2 — discovered live during Block 3.

**What the obvious first fix looks like:**

```python
is_async = inspect.iscoroutinefunction(original)   # ← returns False for AsyncOpenAI
```

**What actually happens:** `AsyncCompletions.create` is `async def` in the
openai SDK source, but the SDK's internal method-wrapping (type overloads,
`@override` from `typing_extensions`, resource class machinery) strips the
`CO_COROUTINE` flag from the accessible bound method. So
`inspect.iscoroutinefunction` returns `False` even though calling
`original()` produces a coroutine. The runtime error is identical to §2.2.

**Confirmed at runtime:**
```
AttributeError: 'coroutine' object has no attribute 'choices'
RuntimeWarning: coroutine 'AsyncCompletions.create' was never awaited
```

**The fix:** check the client type, not the method:

```python
from openai._base_client import AsyncAPIClient  # not in public __init__
is_async = isinstance(client, AsyncAPIClient)
```

`AsyncAPIClient` is the base class for `AsyncOpenAI` and any subclass a
user might build. It is *not* exported from `openai.__init__`, so import it
from `openai._base_client` and treat it as a semi-stable internal.

**v0.1 note:** for truly generic OpenAI-compatible async clients (not
`openai.AsyncOpenAI` subclasses), the v0.1 SDK should accept an explicit
`async_client: bool` flag as an escape hatch:
`aegis.instrument(client, async_client=True)`.

---

## 3. Subtler gotchas (won't crash, but will silently degrade traces)

### 3.1 `stream_options={"include_usage": True}` is opt-in on the server side

Without this flag in the streaming call, the final chunk has `usage=None`.
The wrapper emits the trace but with `tokens_in=None, tokens_out=None,
cost_usd=None`. The trace is still recorded; the cost chart just shows a gap.

**v0.1 decision needed:** should the SDK auto-inject `stream_options` on
every `stream=True` call, or leave it to the user?

- Auto-inject pro: traces are always complete; matches Aegis's promise of
  "zero-config observability."
- Auto-inject con: changes the wire payload the user didn't request; could
  surprise users inspecting raw HTTP traffic; some proxy servers reject
  unknown options.
- **Recommended default for v0.1:** auto-inject, behind a flag
  `instrument_openai(client, auto_usage=True)`. Document the behavior
  prominently.

### 3.2 Patching the class vs the instance

Patching `Completions.create` at the class level (`type(client.chat.completions).create = ...`)
would affect every `OpenAI()` instance in the same Python process. This is
wrong for Aegis because a user may have one instrumented client (production
traces) and one bare client (synthetic calls for eval baselines). Instance
patching keeps the scopes isolated. The sentinel tag is placed on the
instance's attribute, so idempotency is per-instance.

### 3.3 Errors must still emit traces (status="error")

A wrapper that only emits on success silently drops failed calls from the
dashboard timeline. This makes the latency histogram look healthy even when
the service is degraded. Always emit on exception before re-raising:

```python
except Exception as exc:
    _emit(_build_event(..., status="error", response=repr(exc)))
    raise   # never swallow — the caller's error handling must still run
```

### 3.4 Async stream: `_wrap_stream_async` is an async *generator*, not a coroutine

The async wrapped path does:

```python
async def wrapped(...):
    result = await original(...)      # await the SDK call → gets AsyncStream
    if streamed:
        return _wrap_stream_async(result, on_done)   # return async generator
```

`_wrap_stream_async` is declared with `async def ... yield`, so calling it
returns an async generator *object* — it is NOT awaited. The caller does:

```python
stream = await client.chat.completions.create(stream=True, ...)  # awaits `wrapped`
async for chunk in stream:   # iterates the async generator
    ...
```

This is correct. The easy mistake: accidentally putting `await
_wrap_stream_async(...)` inside `wrapped`, which would try to await an
async generator and raise `TypeError: object async_generator can't be used
in 'await' expression`.

---

## 4. v0.1 SDK work items that fell out of this block

Cross-reference against [README §12](../README.md#12-roadmap-versioned-shipping) when v0.1 SDK starts:

1. **`auto_usage` flag** — `instrument_openai(client, auto_usage=True)` silently
   injects `stream_options={"include_usage": True}` on streaming calls; documented
   in SDK README as the behavior.
2. **`async_client` escape hatch** — `instrument_openai(client, async_client=True)`
   forces the async wrapper for any OpenAI-compatible async client that is not a
   subclass of `openai._base_client.AsyncAPIClient`. Needed for httpx-based custom
   clients.
2. **Full messages list in trace** — demo stores only `messages[-1]` as a preview.
   v0.1 should persist the full messages list (JSON-serialized) so the trace detail
   view can replay the conversation.
3. **`traced_at` timestamp** — add an ISO-8601 `traced_at` field to every event so
   the backend can sort traces correctly even if POST batches arrive out of order.
4. **Thread-safety on batch emit** — demo emits inline (print). v0.1's HTTP emitter
   must be thread-safe; concurrent sync calls from a thread pool will race on a
   shared queue or HTTP session.
5. **`deinstrument(client)` / restore original** — store original as a closure
   variable and expose a way to remove the patch. Needed for tests that want to
   assert on the un-wrapped call count.
6. **Model key normalization** — the demo hard-codes `COST_KEY = f"groq/{MODEL}"`.
   v0.1 should extract provider from the `base_url` or a user-supplied `provider=`
   arg and normalize to the `cost.py` catalog format automatically.

---

## 5. What I should be able to defend

1. **"Why monkey-patch the instance and not use a decorator?"** → decorator requires
   the user to change every call site. Aegis's promise is zero call-site changes.
   Instance patch is invisible to the user and scoped to one client.

2. **"What happens to a streaming caller if the wrapper consumes the iterator?"** →
   the caller's `for chunk in stream` loop exits immediately with zero iterations.
   No error. Silent blank output. The fix is a pass-through generator.

3. **"Why does `inspect.iscoroutinefunction` have to run before wrapping, not at
   call time?"** → because by call time you're already inside a `def` or `async def`.
   If you choose `def`, you can't `await` inside it. You must choose the right
   function shape *at instrument time*, not at call time.

4. **"Calling `instrument_openai(client)` twice — what happens?"** → the sentinel
   attribute `_aegis_instrumented=True` is set on the wrapped function. On the
   second call, `getattr(original, _SENTINEL, False)` returns True, and the
   function returns immediately. Same function object, no double-wrapping.

5. **"Why not patch the class instead of the instance?"** → class-level patch would
   affect all `OpenAI()` instances in the process. Eval baselines running on a
   bare client would suddenly emit traces they shouldn't. Instance scope = explicit
   opt-in per client.

---

## 6. What this block intentionally does NOT do

- **Does not wire to the Aegis ingest path.** Sink is `print`. Wiring is a v0.1
  SDK item (`app/sdk/client.py::AegisClient`).
- **Does not handle tool-use or function-calling responses.** `choices[0].message.content`
  is None when the model returns a tool call; the trace emits `response_preview=""`
  silently. v0.1 must handle `tool_calls` in the message.
- **Does not handle context-manager protocol on the OpenAI client.** `openai.OpenAI`
  is also usable as `with OpenAI() as client:`. The patch survives this but is not
  tested here.
- **Does not measure TTFT** (time-to-first-token). `latency_ms` here is end-to-end.
  TTFT is measurable in the stream wrapper (timestamp of first non-empty chunk);
  left for v0.1 trace schema to specify.

---

**Last verified:** 2026-05-11 · all 4 demo paths pass (sync/async × non-stream/stream + idempotency) · script + notes committed to `week0/`.
