# aegis-sdk (Python)

Drop-in tracing for LLM apps. Instruments your existing OpenAI-compatible client — every
call's prompt, completion, tokens, cost, and latency shows up in your Aegis dashboard,
with **zero changes to your call sites**.

Part of [Aegis](../../README.md), the open-source LLM observability platform.

---

## Install

```bash
pip install aegis-sdk
# the SDK instruments whatever OpenAI-compatible client you already use;
# install that yourself, or pull it in via the extra:
pip install "aegis-sdk[openai]"
```

## Quickstart — the two lines

```python
from openai import OpenAI
from aegis_sdk import Aegis

aegis = Aegis(api_key="aegis_live_xxx", project="my-app")   # line 1
client = aegis.instrument(OpenAI())                          # line 2

# ...every call below is now traced. Nothing else changes:
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Works the same for `AsyncOpenAI`, for `stream=True`, and for any OpenAI-compatible
endpoint (Groq, Ollama, …) — point your client at its `base_url` as usual.

## Per-call metadata

Attach context to a specific call with the `aegis_metadata` kwarg. It's stripped before
the request reaches the provider:

```python
client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[...],
    aegis_metadata={"flow": "upi_dispute", "user_tier": "premium"},
)
```

---

## Reliability — Aegis being down never touches your app

Tracing runs on a background daemon thread. Your request thread only ever does an O(1)
in-memory enqueue. Specifically:

| Guarantee | How |
|---|---|
| **Never blocks your call** | Instrumented calls enqueue to an in-memory ring buffer and return immediately. All HTTP happens on a background flush thread. |
| **Never grows unbounded** | The buffer is a bounded ring (default 10k events). If Aegis is unreachable, the *oldest* traces are dropped — your process never OOMs. Drops are counted and logged. |
| **Never crashes your app** | A circuit breaker stops hammering a dead backend after N consecutive failures. The HTTP layer swallows every error — a failed flush returns `False`, never raises. |
| **Never double-counts** | Every event carries an idempotency key, so a retried batch is deduplicated server-side. |
| **Never loses the tail** | An `atexit` hook drains the buffer on interpreter exit. For short-lived scripts, call `aegis.flush()` or use `with Aegis(...) as aegis:`. |

```python
# short-lived script — guarantee the last traces ship:
with Aegis(api_key="aegis_live_xxx") as aegis:
    client = aegis.instrument(OpenAI())
    ...
# flush + clean shutdown happen on block exit
```

---

## Configuration

```python
Aegis(
    api_key="aegis_live_xxx",          # required; must start with "aegis_"
    base_url="http://localhost:8000",  # your Aegis API
    project="my-app",                  # client-side label folded into trace metadata
    flush_interval_s=0.5,              # background flush cadence
    batch_size=100,                    # traces per HTTP request (backend cap: 500)
    buffer_size=10_000,                # ring buffer capacity
    timeout_s=2.0,                     # per-attempt HTTP timeout
    max_attempts=3,                    # retries per batch (exponential backoff)
    fail_threshold=5,                  # consecutive failures before the circuit opens
    reset_timeout_s=30.0,              # how long the circuit stays open
)
```

`instrument()` also takes:

- `provider` — `"groq"`, `"openai"`, … — overrides provider inference from the base URL.
  Drives model-id normalization for correct server-side cost lookup.
- `auto_usage` (default `True`) — injects `stream_options={"include_usage": True}` on
  streaming calls so the trace gets token counts.
- `async_client` — force the async wrapper for an async client that isn't an
  `openai.AsyncOpenAI` subclass.

`aegis.deinstrument(client)` removes the patch and restores the original client.

> **Note on `project`:** it's a client-side label only. The authoritative tenant
> (org + project) is always resolved server-side from your API key.

---

## Known limitation (v0.1)

A streamed response that is *purely* a tool call (no text content) records an empty
`completion` — the stream wrapper accumulates text deltas only. Non-streaming tool calls
are captured fully. Streamed-tool-call reconstruction lands in v0.2.

## License

MIT — see [LICENSE](../../LICENSE).
