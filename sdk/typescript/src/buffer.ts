// Bounded ring buffer of trace events. Drop oldest on overflow; expose the
// dropped count so the flush layer can log it.
//
// Why bounded, not unbounded: if Aegis is unreachable for an hour, an
// unbounded queue grows until the host Node process OOMs — Aegis being down
// would take the customer's app down with it. The bounded ring caps the worst
// case at "lose the oldest traces", never "crash the host."
//
// Unlike the Python SDK, there's no lock — Node's event loop is single-threaded
// and `put` / `drain` are synchronous, so they're atomic by construction.

import type { TraceEvent } from "./trace.js";

export const DEFAULT_MAXLEN = 10_000;

export class RingBuffer {
  private readonly maxlen: number;
  private readonly items: TraceEvent[] = [];
  private droppedCount = 0;

  constructor(maxlen: number = DEFAULT_MAXLEN) {
    if (maxlen < 1) {
      throw new Error("maxlen must be >= 1");
    }
    this.maxlen = maxlen;
  }

  /** Enqueue an event. When at capacity, evict the oldest and count it. */
  put(event: TraceEvent): void {
    if (this.items.length >= this.maxlen) {
      this.items.shift();
      this.droppedCount++;
    }
    this.items.push(event);
  }

  /** Remove and return up to `maxN` events, oldest first. */
  drain(maxN: number): TraceEvent[] {
    if (maxN < 1) {
      throw new Error("maxN must be >= 1");
    }
    return this.items.splice(0, maxN);
  }

  get length(): number {
    return this.items.length;
  }

  /** Total overflow drops since construction (monotonic). */
  get dropped(): number {
    return this.droppedCount;
  }
}
