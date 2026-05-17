# @aegis/sdk (TypeScript)

Drop-in tracing for LLM apps. Instruments your existing OpenAI-compatible client — every call's prompt, completion, tokens, cost, and latency shows up in your Aegis dashboard, with **zero changes to your call sites**.

Part of [Aegis](../../README.md), the open-source LLM observability platform. TypeScript SDK; feature parity with [`aegis-sdk`](../python) (Python).

---

## Install

```bash
npm install @aegis/sdk
# The SDK instruments whatever OpenAI-compatible client you already use;
# install that yourself (it's a peer dep, not bundled):
npm install openai
```

## Quickstart — the two lines

```ts
import OpenAI from "openai";
import { Aegis } from "@aegis/sdk";

const aegis = new Aegis({ apiKey: "aegis_live_xxx", project: "my-app" }); // line 1
const client = aegis.instrument(new OpenAI());                            // line 2

// ...every call below is now traced. Nothing else changes:
const resp = await client.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Hello" }],
});
```

Works the same for `stream: true`, for tool-calls, and for any OpenAI-compatible endpoint (Groq, Ollama, …) — point your client at its `baseURL` as usual.

## Per-call metadata

Attach context to a specific call with `aegis_metadata`. It's stripped before the request reaches the provider:

```ts
await client.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [...],
  aegis_metadata: { flow: "upi_dispute", user_tier: "premium" },
} as any); // OpenAI's strict types don't know about aegis_metadata
```

---

## Reliability — Aegis being down never touches your app

Tracing runs on a background `setInterval` timer. Your code path only ever does an O(1) in-memory enqueue. Specifically:

| Guarantee | How |
|---|---|
| **Never blocks your call** | Instrumented calls enqueue to an in-memory ring buffer and return immediately. All HTTP happens on a background timer. |
| **Never grows unbounded** | The buffer is a bounded ring (default 10 000 events). If Aegis is unreachable, the *oldest* traces are dropped. Drops are counted and logged once Aegis recovers. |
| **Never crashes your app** | A circuit breaker stops hammering a dead backend after N consecutive failures. The HTTP layer swallows every error — a failed flush returns `false`, never throws. |
| **Never double-counts** | Every event carries an idempotency key, so a retried batch is deduplicated server-side. |
| **Never holds the event loop** | The flush timer is `.unref()`'d, so your script exits naturally. A `beforeExit` hook drains the buffer before Node finishes — for short-lived scripts, call `await aegis.close()` to guarantee it. |

```ts
// Short-lived script — guarantee the last traces ship:
const aegis = new Aegis({ apiKey: "aegis_live_xxx" });
try {
  const client = aegis.instrument(new OpenAI());
  // ...
} finally {
  await aegis.close();
}
```

---

## Configuration

```ts
new Aegis({
  apiKey: "aegis_live_xxx",          // required; must start with "aegis_"
  baseUrl: "http://localhost:8000",  // your Aegis API
  project: "my-app",                 // client-side label folded into trace metadata
  flushIntervalMs: 500,              // background flush cadence
  batchSize: 100,                    // traces per HTTP request (backend cap: 500)
  bufferSize: 10_000,                // ring buffer capacity
  timeoutMs: 2_000,                  // per-attempt HTTP timeout
  maxAttempts: 3,                    // retries per batch (exponential backoff)
  failThreshold: 5,                  // consecutive failures before the circuit opens
  resetTimeoutMs: 30_000,            // how long the circuit stays open
});
```

`aegis.instrument(client, opts?)` also accepts:

- `provider` — `"groq"`, `"openai"`, … — overrides provider inference from `client.baseURL`. Drives model-id normalization for correct server-side cost lookup.
- `autoUsage` (default `true`) — injects `stream_options.include_usage = true` on streaming calls so the trace gets token counts.

`aegis.deinstrument(client)` removes the patch.

> **Note on `project`:** it's a client-side label only. The authoritative tenant (org + project) is always resolved server-side from your API key.

---

## Known limitation (v0.2)

A streamed response that is *purely* a tool call (no text content) records an empty `completion` — the stream wrapper accumulates text deltas only. Non-streaming tool calls are captured fully. Streamed-tool-call reconstruction lands in v0.3.

## License

MIT — see [LICENSE](../../LICENSE).
