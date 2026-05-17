import { describe, expect, it } from "vitest";

import { Aegis } from "../src/client.js";
import type { TraceEvent } from "../src/trace.js";

import { FakeClient, makeResponse } from "./_fakes.js";

/** Stand-in for Transport — records what was sent, never touches the network.
 *  Mirrors sdk/python/tests/_fakes.py:FakeTransport. */
class FakeTransport {
  readonly batches: TraceEvent[][] = [];
  readonly sent: TraceEvent[] = [];
  succeed = true;
  // The real Transport doesn't have a close(); Aegis doesn't call one in TS.
  async sendBatch(events: TraceEvent[]): Promise<boolean> {
    this.batches.push([...events]);
    this.sent.push(...events);
    return this.succeed;
  }
}

function makeAegis(transport: FakeTransport, overrides: Partial<{
  project: string;
  batchSize: number;
  autostart: boolean;
}> = {}): Aegis {
  return new Aegis({
    apiKey: "aegis_dev_testkey",
    project: overrides.project,
    batchSize: overrides.batchSize,
    _transport: transport as unknown as import("../src/transport.js").Transport,
    _autostart: overrides.autostart ?? false,
  });
}

describe("Aegis facade", () => {
  it("rejects a malformed api key", () => {
    expect(
      () => new Aegis({ apiKey: "not-a-real-key", _transport: new FakeTransport() as never, _autostart: false }),
    ).toThrow(/aegis_/);
  });

  it("rejects an out-of-range batch size", () => {
    expect(() => makeAegis(new FakeTransport(), { batchSize: 999 })).toThrow(/batchSize/);
  });

  it("instrumented call reaches the transport on flush", async () => {
    const transport = new FakeTransport();
    const aegis = makeAegis(transport);
    const client = new FakeClient(makeResponse("hello", { prompt_tokens: 7, completion_tokens: 3 }));
    aegis.instrument(client);

    await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "hi" }],
    });
    expect(transport.sent.length).toBe(0); // buffered, not shipped

    await aegis.flush();
    expect(transport.sent.length).toBe(1);
    expect(transport.sent[0]!.completion).toBe("hello");
    await aegis.close();
  });

  it("stamps project label into metadata", async () => {
    const transport = new FakeTransport();
    const aegis = makeAegis(transport, { project: "finpal-prod" });
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    aegis.instrument(client);

    await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] });
    await aegis.flush();
    expect(transport.sent[0]!.metadata.project).toBe("finpal-prod");
    await aegis.close();
  });

  it("per-call project (via aegis_metadata) overrides the client-wide default", async () => {
    const transport = new FakeTransport();
    const aegis = makeAegis(transport, { project: "finpal-prod" });
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    aegis.instrument(client);

    await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [],
      aegis_metadata: { project: "finpal-staging" },
    });
    await aegis.flush();
    expect(transport.sent[0]!.metadata.project).toBe("finpal-staging");
    await aegis.close();
  });

  it("flush drains in batchSize chunks", async () => {
    const transport = new FakeTransport();
    const aegis = makeAegis(transport, { batchSize: 2 });
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    aegis.instrument(client);

    for (let i = 0; i < 5; i++) {
      await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] });
    }
    await aegis.flush();

    expect(transport.batches.map((b) => b.length)).toEqual([2, 2, 1]);
    expect(transport.sent.length).toBe(5);
    await aegis.close();
  });

  it("flush stops early on the first failed send", async () => {
    const transport = new FakeTransport();
    transport.succeed = false; // every batch fails
    const aegis = makeAegis(transport, { batchSize: 2 });
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    aegis.instrument(client);

    for (let i = 0; i < 6; i++) {
      await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] });
    }
    await aegis.flush();

    // Stopped after the first failed batch — the rest stays buffered.
    expect(transport.batches.length).toBe(1);
    expect(transport.batches[0]!.length).toBe(2);
    await aegis.close();
  });

  it("close is idempotent", async () => {
    const aegis = makeAegis(new FakeTransport());
    await aegis.close();
    await aegis.close(); // second call is a no-op, not an error
  });

  it("close performs a final flush", async () => {
    const transport = new FakeTransport();
    const aegis = makeAegis(transport);
    const client = new FakeClient(makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }));
    aegis.instrument(client);

    await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] });
    await aegis.close(); // never called flush() — close() must still ship it
    expect(transport.sent.length).toBe(1);
  });

  it("background timer flushes without an explicit call", async () => {
    const transport = new FakeTransport();
    const aegis = new Aegis({
      apiKey: "aegis_dev_testkey",
      flushIntervalMs: 20,
      _transport: transport as unknown as import("../src/transport.js").Transport,
      // autostart on (default) — daemon timer should drain on its own
    });
    try {
      const client = new FakeClient(
        makeResponse("x", { prompt_tokens: 1, completion_tokens: 1 }),
      );
      aegis.instrument(client);
      await client.chat.completions.create({ model: "gpt-4o-mini", messages: [] });

      const deadline = Date.now() + 2_000;
      while (transport.sent.length === 0 && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 20));
      }
      expect(transport.sent.length).toBe(1);
    } finally {
      await aegis.close();
    }
  });
});
