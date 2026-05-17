// The trace event — what the SDK buffers and ships to Aegis.
//
// Same wire contract as the Python SDK's `TraceEvent.to_payload()`, so the
// backend can't tell the two clients apart:
//   * the full `messages` list is JSON-serialized into `prompt`
//   * `status` / `streamed` / `provider` / `error` ride under `metadata.aegis`
//   * `cost_usd` is never sent — server is the source of truth
//
// Mapping decisions live in the Python `trace.py` module docstring; this is
// the TS port, intentionally identical in shape.

import { randomUUID } from "node:crypto";

export interface TraceMetadata {
  [key: string]: unknown;
}

export interface ChatMessage {
  role: string;
  content?: string | null;
  [key: string]: unknown;
}

/** What the backend's `TraceIngest` schema accepts on the wire. */
export interface TraceWirePayload {
  trace_id: string;
  ts: string;
  model: string;
  prompt: string;
  completion: string;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  metadata: TraceMetadata;
  idempotency_key: string;
}

/** Constructor input — what an instrumentation wrapper captures from a call. */
export interface TraceEventInput {
  model: string;
  messages: ChatMessage[];
  completion: string;
  tokensIn?: number | null;
  tokensOut?: number | null;
  latencyMs: number;
  status: "ok" | "error";
  streamed: boolean;
  provider?: string | null;
  error?: string | null;
  userMetadata?: TraceMetadata;
}

export class TraceEvent {
  readonly traceId: string;
  readonly idempotencyKey: string;
  readonly ts: string;
  readonly model: string;
  readonly prompt: string;
  readonly completion: string;
  readonly tokensIn: number;
  readonly tokensOut: number;
  readonly latencyMs: number;
  readonly status: "ok" | "error";
  readonly streamed: boolean;
  readonly provider: string | null;
  readonly error: string | null;
  readonly metadata: TraceMetadata;

  constructor(input: TraceEventInput) {
    // trace_id and idempotency_key are minted client-side: the dashboard URL
    // (trace_id) and SDK retry dedupe (idempotency_key) both want one UUID per
    // event. They start equal; kept as separate fields so a future SDK could
    // diverge them if needed.
    this.traceId = randomUUID();
    this.idempotencyKey = this.traceId;
    this.ts = new Date().toISOString();
    this.model = input.model;
    this.prompt = JSON.stringify(input.messages);
    this.completion = input.completion;
    // Streaming calls without include_usage report null tokens. Coerce to 0 so
    // the backend's `ge=0` validator passes and cost calc just yields $0
    // rather than rejecting the whole trace.
    this.tokensIn = input.tokensIn ?? 0;
    this.tokensOut = input.tokensOut ?? 0;
    this.latencyMs = Math.round(input.latencyMs);
    this.status = input.status;
    this.streamed = input.streamed;
    this.provider = input.provider ?? null;
    this.error = input.error ?? null;
    this.metadata = { ...(input.userMetadata ?? {}) };
  }

  /** Render to the backend `TraceIngest` wire shape. SDK-owned fields live
   *  under `metadata.aegis.*` to avoid colliding with user metadata keys. */
  toPayload(): TraceWirePayload {
    const meta: TraceMetadata = { ...this.metadata };
    const aegisBlock: TraceMetadata = {
      status: this.status,
      streamed: this.streamed,
      provider: this.provider,
    };
    if (this.error !== null) {
      aegisBlock.error = this.error;
    }
    meta.aegis = aegisBlock;
    return {
      trace_id: this.traceId,
      ts: this.ts,
      model: this.model,
      prompt: this.prompt,
      completion: this.completion,
      tokens_in: this.tokensIn,
      tokens_out: this.tokensOut,
      latency_ms: this.latencyMs,
      metadata: meta,
      idempotency_key: this.idempotencyKey,
    };
  }
}
