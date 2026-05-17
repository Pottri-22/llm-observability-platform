// The `Aegis` facade — what a customer constructs.
//
//   import { Aegis } from "@aegis/sdk";
//   const aegis = new Aegis({ apiKey: "aegis_live_...", project: "finpal-prod" });
//   const client = aegis.instrument(new OpenAI());
//
// Ties the three TS-SDK-A primitives and TS-SDK-B's instrumentation together:
//
//   instrument() → RingBuffer → [setInterval flush] → Transport → Aegis API
//
// Critical TS-specific bits:
//   * `setInterval(...).unref()` — the timer must NOT keep Node alive on its
//     own. Without unref, the user's app would never exit.
//   * `process.once("beforeExit", ...)` — drains buffered traces before exit.
//     beforeExit doesn't fire on `process.exit()` or fatal crashes; for short
//     scripts, call `await aegis.close()` explicitly.

import { RingBuffer } from "./buffer.js";
import { CircuitBreaker } from "./circuit.js";
import { deinstrument as freeDeinstrument, instrument as freeInstrument } from "./instrument.js";
import type { OpenAILike } from "./instrument.js";
import type { TraceEvent } from "./trace.js";
import { Transport } from "./transport.js";

// The backend's TraceBatch caps at 500 traces per request. Never drain past.
const MAX_BATCH = 500;

export interface AegisOpts {
  /** Required. Must start with `aegis_`. */
  apiKey: string;
  /** Aegis API base URL. Default `http://localhost:8000`. */
  baseUrl?: string;
  /** Client-side label folded into every trace's metadata.project. The
   *  authoritative tenant is always resolved server-side from the API key. */
  project?: string;
  /** How often the background timer drains the buffer, in ms. Default 500. */
  flushIntervalMs?: number;
  /** Traces per HTTP request. Default 100. Backend cap is 500. */
  batchSize?: number;
  /** Ring buffer capacity. Default 10_000. */
  bufferSize?: number;
  /** Per-attempt HTTP timeout, in ms. Default 2000. */
  timeoutMs?: number;
  /** Retries per batch (exponential backoff). Default 3. */
  maxAttempts?: number;
  /** Consecutive failures before the circuit opens. Default 5. */
  failThreshold?: number;
  /** How long the circuit stays open, in ms. Default 30000. */
  resetTimeoutMs?: number;

  // Test injection — not part of the public API contract.
  /** @internal */ _transport?: Transport;
  /** @internal */ _autostart?: boolean;
}

export interface InstrumentArgs {
  /** Override provider inference from the client's baseURL. */
  provider?: string;
  /** Inject `stream_options.include_usage` on streaming calls. Default true. */
  autoUsage?: boolean;
}

export class Aegis {
  private readonly buffer: RingBuffer;
  private readonly breaker: CircuitBreaker;
  private readonly transport: Transport;
  private readonly batchSize: number;
  private readonly flushIntervalMs: number;
  private readonly project: string | null;
  private flushTimer: NodeJS.Timeout | null = null;
  private flushing = false;
  private closed = false;
  private lastDropped = 0;

  constructor(opts: AegisOpts) {
    // Fail fast: a malformed key means every trace would 401 silently. Better
    // to throw at construction than discover it never worked in prod.
    if (!opts.apiKey || !opts.apiKey.startsWith("aegis_")) {
      throw new Error("apiKey must be a non-empty Aegis key (starts with 'aegis_').");
    }
    const batchSize = opts.batchSize ?? 100;
    if (batchSize < 1 || batchSize > MAX_BATCH) {
      throw new Error(`batchSize must be between 1 and ${MAX_BATCH}`);
    }
    this.batchSize = batchSize;
    this.flushIntervalMs = opts.flushIntervalMs ?? 500;
    this.project = opts.project ?? null;
    this.buffer = new RingBuffer(opts.bufferSize ?? 10_000);
    this.breaker = new CircuitBreaker({
      failThreshold: opts.failThreshold ?? 5,
      resetTimeoutMs: opts.resetTimeoutMs ?? 30_000,
    });
    this.transport =
      opts._transport ??
      new Transport({
        baseUrl: opts.baseUrl ?? "http://localhost:8000",
        apiKey: opts.apiKey,
        breaker: this.breaker,
        timeoutMs: opts.timeoutMs ?? 2_000,
        maxAttempts: opts.maxAttempts ?? 3,
      });

    if (opts._autostart !== false) {
      this.startFlushTimer();
    }
    // One-shot drain on interpreter shutdown. process.once removes itself
    // after firing; we also removeListener in close() so a long-lived process
    // with many Aegis instances doesn't trip the >10-listeners warning.
    process.once("beforeExit", this.onBeforeExit);
  }

  // -- public API -----------------------------------------------------------

  instrument<T extends OpenAILike>(client: T, args: InstrumentArgs = {}): T {
    return freeInstrument(client, {
      sink: this.sink,
      provider: args.provider,
      autoUsage: args.autoUsage,
    });
  }

  deinstrument(client: OpenAILike): boolean {
    return freeDeinstrument(client);
  }

  /** Synchronously drain and ship everything currently buffered. */
  async flush(): Promise<void> {
    await this.flushOnce();
  }

  /** Stop the flush timer, ship a final batch. Idempotent — safe to call
   *  explicitly *and* have it run via beforeExit. */
  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    if (this.flushTimer !== null) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
    await this.flushOnce();
    process.removeListener("beforeExit", this.onBeforeExit);
  }

  // -- internals ------------------------------------------------------------

  /** Sink passed to instrument(). Stamps the client-wide project label, then
   *  enqueues. Per-call `aegis_metadata: { project: "..." }` always wins
   *  because we only set the default when the key isn't already present. */
  private readonly sink = (event: TraceEvent): void => {
    if (this.project !== null && !("project" in event.metadata)) {
      (event.metadata as Record<string, unknown>).project = this.project;
    }
    this.buffer.put(event);
  };

  private readonly onBeforeExit = (): void => {
    void this.close();
  };

  private startFlushTimer(): void {
    const timer = setInterval(() => {
      // Fire-and-forget: never let a flush bug kill the timer or surface as
      // an unhandled rejection.
      void this.flushOnce().catch(() => undefined);
    }, this.flushIntervalMs);
    // Crucial: without unref the timer keeps Node's event loop alive forever.
    timer.unref();
    this.flushTimer = timer;
  }

  /** Drain in batchSize chunks; stop early on first failed send.
   *
   *  During an outage, naively draining the whole buffer into a dead transport
   *  would dump every trace into the void. Stopping early leaves the rest
   *  buffered so it can ride out the outage until the circuit recovers. */
  private async flushOnce(): Promise<void> {
    if (this.flushing) return; // simple re-entry guard
    this.flushing = true;
    try {
      this.logDrops();
      while (true) {
        const batch = this.buffer.drain(this.batchSize);
        if (batch.length === 0) break;
        const ok = await this.transport.sendBatch(batch);
        if (!ok) break;
      }
    } finally {
      this.flushing = false;
    }
  }

  /** Surface ring-buffer overflow — a sign Aegis has been unreachable for a
   *  while. We don't carry a structured logger in the SDK; console.warn is
   *  appropriate for a one-line operational signal. */
  private logDrops(): void {
    const dropped = this.buffer.dropped;
    if (dropped > this.lastDropped) {
      console.warn(
        `aegis: dropped ${dropped - this.lastDropped} trace(s) to buffer overflow ` +
          "(Aegis may be unreachable)",
      );
      this.lastDropped = dropped;
    }
  }
}
