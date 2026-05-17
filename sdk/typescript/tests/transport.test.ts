import { describe, expect, it } from "vitest";

import { CircuitBreaker } from "../src/circuit.js";
import { TraceEvent } from "../src/trace.js";
import { Transport } from "../src/transport.js";
import type { FetchFn } from "../src/transport.js";

const noSleep = async (): Promise<void> => undefined; // skip backoff in tests

function event(): TraceEvent {
  return new TraceEvent({
    model: "gpt-4o-mini",
    messages: [],
    completion: "",
    latencyMs: 0,
    status: "ok",
    streamed: false,
  });
}

function make(handler: FetchFn, opts: {
  breaker?: CircuitBreaker;
  maxAttempts?: number;
} = {}): { transport: Transport; breaker: CircuitBreaker; calls: number[] } {
  const breaker = opts.breaker ?? new CircuitBreaker();
  const calls: number[] = [];
  const counted: FetchFn = async (input, init) => {
    calls.push(1);
    return handler(input, init);
  };
  const transport = new Transport({
    baseUrl: "http://test",
    apiKey: "aegis_dev_x",
    breaker,
    maxAttempts: opts.maxAttempts ?? 3,
    fetchFn: counted,
    sleepFn: noSleep,
  });
  return { transport, breaker, calls };
}

describe("Transport.sendBatch", () => {
  it("treats an empty batch as a no-op success", async () => {
    const { transport, calls } = make(async () => new Response(null, { status: 500 }));
    expect(await transport.sendBatch([])).toBe(true);
    expect(calls.length).toBe(0); // never touched the wire
  });

  it("returns true on 2xx and resets the breaker", async () => {
    const { transport, breaker } = make(
      async () => new Response(JSON.stringify({ accepted: [] }), { status: 201 }),
    );
    expect(await transport.sendBatch([event()])).toBe(true);
    expect(breaker.getState()).toBe("closed");
  });

  it("retries 5xx then trips the breaker on exhaustion", async () => {
    const { transport, breaker, calls } = make(
      async () => new Response("oops", { status: 503 }),
      { breaker: new CircuitBreaker({ failThreshold: 1 }), maxAttempts: 3 },
    );
    expect(await transport.sendBatch([event()])).toBe(false);
    expect(calls.length).toBe(3);
    expect(breaker.getState()).toBe("open");
  });

  it("does NOT retry 4xx and does NOT trip the breaker", async () => {
    const { transport, breaker, calls } = make(
      async () => new Response("invalid api key", { status: 401 }),
      { breaker: new CircuitBreaker({ failThreshold: 1 }), maxAttempts: 3 },
    );
    expect(await transport.sendBatch([event()])).toBe(false);
    expect(calls.length).toBe(1); // not retried — the request itself is wrong
    expect(breaker.getState()).toBe("closed"); // Aegis is healthy
  });

  it("retries network errors", async () => {
    const { transport, breaker, calls } = make(
      async () => {
        throw new TypeError("fetch failed");
      },
      { breaker: new CircuitBreaker({ failThreshold: 1 }), maxAttempts: 2 },
    );
    expect(await transport.sendBatch([event()])).toBe(false);
    expect(calls.length).toBe(2);
    expect(breaker.getState()).toBe("open");
  });

  it("skips the network entirely when the breaker is OPEN", async () => {
    const breaker = new CircuitBreaker({ failThreshold: 1 });
    breaker.recordFailure(); // trip OPEN
    const { transport, calls } = make(
      async () => new Response(null, { status: 201 }),
      { breaker },
    );
    expect(await transport.sendBatch([event()])).toBe(false);
    expect(calls.length).toBe(0);
  });
});
