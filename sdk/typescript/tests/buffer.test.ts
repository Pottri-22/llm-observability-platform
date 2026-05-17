import { describe, expect, it } from "vitest";

import { RingBuffer } from "../src/buffer.js";
import { TraceEvent } from "../src/trace.js";

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

describe("RingBuffer", () => {
  it("delivers events in FIFO order", () => {
    const buf = new RingBuffer(10);
    const events = [event(), event(), event()];
    for (const e of events) buf.put(e);

    expect(buf.length).toBe(3);
    expect(buf.drain(10)).toEqual(events);
    expect(buf.length).toBe(0);
  });

  it("drain respects maxN", () => {
    const buf = new RingBuffer(10);
    for (let i = 0; i < 5; i++) buf.put(event());
    expect(buf.drain(2).length).toBe(2);
    expect(buf.length).toBe(3);
  });

  it("drain on empty returns []", () => {
    expect(new RingBuffer().drain(5)).toEqual([]);
  });

  it("drops the oldest on overflow and counts the drops", () => {
    const buf = new RingBuffer(3);
    const events = [event(), event(), event(), event(), event()];
    for (const e of events) buf.put(e);

    expect(buf.length).toBe(3);
    expect(buf.dropped).toBe(2);
    expect(buf.drain(10)).toEqual(events.slice(2)); // newest three survived
  });

  it("rejects a non-positive maxlen", () => {
    expect(() => new RingBuffer(0)).toThrow(/maxlen/);
  });
});
