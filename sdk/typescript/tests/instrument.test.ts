import { describe, expect, it } from "vitest";

import {
  deinstrument,
  extractCompletion,
  instrument,
  normalizeModel,
  resolveProvider,
} from "../src/instrument.js";
import type { TraceEvent } from "../src/trace.js";

import { FakeClient, makeChunk, makeResponse } from "./_fakes.js";

const SENTINEL = Symbol.for("aegis.instrumented");

// --- pure helpers -----------------------------------------------------------

describe("resolveProvider", () => {
  it("explicit override wins", () => {
    expect(resolveProvider("anthropic", "https://api.groq.com/openai/v1")).toBe("anthropic");
  });

  it("infers from baseURL host", () => {
    expect(resolveProvider(undefined, "https://api.groq.com/openai/v1")).toBe("groq");
    expect(resolveProvider(undefined, "https://api.openai.com/v1")).toBe("openai");
    expect(resolveProvider(undefined, "https://api.anthropic.com")).toBe("anthropic");
  });

  it("returns null when nothing matches", () => {
    expect(resolveProvider(undefined, "https://example.com")).toBe(null);
    expect(resolveProvider(undefined, null)).toBe(null);
  });
});

describe("normalizeModel", () => {
  it("leaves an already-prefixed model alone", () => {
    expect(normalizeModel("groq/llama-3.3-70b-versatile", "groq")).toBe(
      "groq/llama-3.3-70b-versatile",
    );
  });

  it("prefixes a bare Groq model", () => {
    expect(normalizeModel("llama-3.3-70b-versatile", "groq")).toBe(
      "groq/llama-3.3-70b-versatile",
    );
  });

  it("leaves OpenAI/Anthropic bare (cost catalog keys them bare)", () => {
    expect(normalizeModel("gpt-4o-mini", "openai")).toBe("gpt-4o-mini");
    expect(normalizeModel("claude-sonnet-4-6", "anthropic")).toBe("claude-sonnet-4-6");
  });
});

describe("extractCompletion", () => {
  it("returns message.content when present", () => {
    expect(extractCompletion(makeResponse("hello"))).toBe("hello");
  });

  it("serializes tool calls into completion when content is null", () => {
    const tc = {
      id: "call_1",
      type: "function" as const,
      function: { name: "get_weather", arguments: '{"city":"Pune"}' },
    };
    const out = extractCompletion(makeResponse(null, null, [tc]));
    expect(JSON.parse(out)[0]).toMatchObject({
      id: "call_1",
      name: "get_weather",
      arguments: '{"city":"Pune"}',
    });
  });

  it("returns empty string when neither content nor tool_calls", () => {
    expect(extractCompletion(makeResponse(null, null))).toBe("");
  });
});

// --- end-to-end instrument behaviour -----------------------------------------

describe("instrument (non-streaming)", () => {
  it("emits one trace and returns the real response", async () => {
    const events: TraceEvent[] = [];
    const client = new FakeClient(
      makeResponse("hello", { prompt_tokens: 7, completion_tokens: 3 }),
    );
    instrument(client, { sink: (e) => events.push(e) });

    const resp = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "hi" }],
    });

    expect(resp.choices[0].message.content).toBe("hello");
    expect(events.length).toBe(1);
    const ev = events[0]!;
    expect(ev.model).toBe("gpt-4o-mini");
    expect(ev.completion).toBe("hello");
    expect(ev.tokensIn).toBe(7);
    expect(ev.tokensOut).toBe(3);
    expect(ev.status).toBe("ok");
    expect(ev.streamed).toBe(false);
    expect(JSON.parse(ev.prompt)).toEqual([{ role: "user", content: "hi" }]);
  });

  it("is idempotent across repeat calls", () => {
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    instrument(client, { sink: () => undefined });
    const first = client.chat.completions.create;
    instrument(client, { sink: () => undefined });
    expect(client.chat.completions.create).toBe(first);
  });

  it("deinstrument removes the patch", async () => {
    const events: TraceEvent[] = [];
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    instrument(client, { sink: (e) => events.push(e) });
    expect((client.chat.completions.create as unknown as { [k: symbol]: unknown })[SENTINEL]).toBe(true);

    expect(deinstrument(client)).toBe(true);
    expect((client.chat.completions.create as unknown as { [k: symbol]: unknown })[SENTINEL]).toBeUndefined();

    await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] });
    expect(events.length).toBe(0); // un-instrumented call emits nothing

    expect(deinstrument(client)).toBe(false); // already clean
  });

  it("emits status=error and re-throws when the call fails", async () => {
    const events: TraceEvent[] = [];
    const client = new FakeClient(new Error("boom"));
    instrument(client, { sink: (e) => events.push(e) });

    await expect(
      client.chat.completions.create({
        model: "gpt-4o-mini",
        messages: [{ role: "user", content: "hi" }],
      }),
    ).rejects.toThrow("boom");

    expect(events.length).toBe(1);
    expect(events[0]!.status).toBe("error");
    expect(events[0]!.error).toContain("boom");
  });

  it("aegis_metadata kwarg is popped and attached to the trace", async () => {
    const events: TraceEvent[] = [];
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    instrument(client, { sink: (e) => events.push(e) });

    await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [],
      aegis_metadata: { flow: "checkout" },
    });

    // never forwarded to the provider
    expect("aegis_metadata" in client.receivedParams[0]).toBe(false);
    // attached to the trace
    expect(events[0]!.metadata.flow).toBe("checkout");
  });

  it("sink exception does not break the caller", async () => {
    const client = new FakeClient(
      makeResponse("still-works", { prompt_tokens: 1, completion_tokens: 1 }),
    );
    instrument(client, {
      sink: () => {
        throw new Error("sink is down");
      },
    });

    const resp = await client.chat.completions.create({ model: "x", messages: [] });
    expect(resp.choices[0].message.content).toBe("still-works");
  });
});

describe("instrument — model normalization", () => {
  it("Groq baseURL prefixes a bare model id", async () => {
    const events: TraceEvent[] = [];
    const client = new FakeClient(
      makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }),
      "https://api.groq.com/openai/v1",
    );
    instrument(client, { sink: (e) => events.push(e) });
    await client.chat.completions.create({ model: "llama-3.3-70b-versatile", messages: [] });
    expect(events[0]!.model).toBe("groq/llama-3.3-70b-versatile");
    expect(events[0]!.provider).toBe("groq");
  });

  it("already-prefixed model passes through", async () => {
    const events: TraceEvent[] = [];
    const client = new FakeClient(
      makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }),
      "https://api.groq.com/openai/v1",
    );
    instrument(client, { sink: (e) => events.push(e) });
    await client.chat.completions.create({ model: "groq/llama-3.3-70b-versatile", messages: [] });
    expect(events[0]!.model).toBe("groq/llama-3.3-70b-versatile"); // no double prefix
  });

  it("OpenAI baseURL leaves model bare", async () => {
    const events: TraceEvent[] = [];
    const client = new FakeClient(
      makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }),
      "https://api.openai.com/v1",
    );
    instrument(client, { sink: (e) => events.push(e) });
    await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] });
    expect(events[0]!.model).toBe("gpt-4o-mini");
  });
});

describe("instrument — streaming", () => {
  it("autoUsage injects stream_options.include_usage on streaming calls", async () => {
    const client = new FakeClient([
      makeChunk("hi"),
      makeChunk(null, { prompt_tokens: 5, completion_tokens: 1 }, false),
    ]);
    instrument(client, { sink: () => undefined });
    const stream = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [],
      stream: true,
    });
    for await (const _ of stream) {
      void _;
    }
    expect(client.receivedParams[0].stream_options).toEqual({ include_usage: true });
  });

  it("autoUsage:false leaves the call untouched", async () => {
    const client = new FakeClient([makeChunk("hi")]);
    instrument(client, { sink: () => undefined, autoUsage: false });
    const stream = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [],
      stream: true,
    });
    for await (const _ of stream) {
      void _;
    }
    expect("stream_options" in client.receivedParams[0]).toBe(false);
  });

  it("passes every chunk through and emits one trace on completion", async () => {
    const events: TraceEvent[] = [];
    const chunks = [
      makeChunk("Hel"),
      makeChunk("lo"),
      makeChunk(null, { prompt_tokens: 4, completion_tokens: 2 }, false),
    ];
    const client = new FakeClient(chunks);
    instrument(client, { sink: (e) => events.push(e) });

    const stream = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [],
      stream: true,
    });
    const received: unknown[] = [];
    for await (const chunk of stream) {
      received.push(chunk);
    }

    expect(received.length).toBe(3); // every chunk visible to caller
    expect(events.length).toBe(1);
    const ev = events[0]!;
    expect(ev.completion).toBe("Hello");
    expect(ev.tokensIn).toBe(4);
    expect(ev.tokensOut).toBe(2);
    expect(ev.streamed).toBe(true);
  });
});
