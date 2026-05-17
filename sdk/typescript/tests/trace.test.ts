import { describe, expect, it } from "vitest";

import { TraceEvent } from "../src/trace.js";

describe("TraceEvent", () => {
  it("mints trace_id and uses it as idempotency_key by default", () => {
    const e = new TraceEvent({
      model: "gpt-4o-mini",
      messages: [],
      completion: "",
      latencyMs: 0,
      status: "ok",
      streamed: false,
    });
    expect(e.traceId).toMatch(/^[0-9a-f-]{36}$/);
    expect(e.idempotencyKey).toBe(e.traceId);
  });

  it("serializes the full messages list into prompt", () => {
    const messages = [
      { role: "system", content: "be terse" },
      { role: "user", content: "hi" },
    ];
    const e = new TraceEvent({
      model: "gpt-4o-mini",
      messages,
      completion: "hello",
      latencyMs: 120,
      status: "ok",
      streamed: false,
    });
    expect(JSON.parse(e.prompt)).toEqual(messages);
  });

  it("coerces null tokens to zero so backend ge=0 validator passes", () => {
    const e = new TraceEvent({
      model: "x",
      messages: [],
      completion: "",
      tokensIn: null,
      tokensOut: null,
      latencyMs: 0,
      status: "ok",
      streamed: true,
    });
    expect(e.tokensIn).toBe(0);
    expect(e.tokensOut).toBe(0);
  });

  it("rounds float latency to int", () => {
    const e = new TraceEvent({
      model: "x",
      messages: [],
      completion: "",
      latencyMs: 120.7,
      status: "ok",
      streamed: false,
    });
    expect(e.latencyMs).toBe(121);
  });

  it("maps to the backend wire shape and folds SDK fields into metadata.aegis", () => {
    const e = new TraceEvent({
      model: "groq/llama-3.3-70b-versatile",
      messages: [{ role: "user", content: "hi" }],
      completion: "hello",
      tokensIn: 12,
      tokensOut: 4,
      latencyMs: 90,
      status: "error",
      streamed: true,
      provider: "groq",
      error: "ReadTimeout",
      userMetadata: { flow: "demo" },
    });
    const payload = e.toPayload();

    expect(payload.model).toBe("groq/llama-3.3-70b-versatile");
    expect(payload.tokens_in).toBe(12);
    expect(payload.idempotency_key).toBe(e.traceId);
    expect("cost_usd" in payload).toBe(false); // server is the source of truth
    expect(payload.metadata.flow).toBe("demo");

    const aegis = payload.metadata.aegis as Record<string, unknown>;
    expect(aegis.status).toBe("error");
    expect(aegis.streamed).toBe(true);
    expect(aegis.provider).toBe("groq");
    expect(aegis.error).toBe("ReadTimeout");
  });

  it("omits the error key from metadata.aegis when status is ok", () => {
    const e = new TraceEvent({
      model: "x",
      messages: [],
      completion: "",
      latencyMs: 0,
      status: "ok",
      streamed: false,
    });
    const aegis = e.toPayload().metadata.aegis as Record<string, unknown>;
    expect("error" in aegis).toBe(false);
  });
});
