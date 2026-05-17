// HTTP transport — ships batches to POST /v1/traces/batch via Node's built-in
// fetch. Retry with exponential backoff, circuit-breaker gated, never throws.
//
// One rule: the customer's app must never be slowed or broken by Aegis being
// unreachable. So:
//   * `sendBatch` returns Promise<boolean>; failure paths log + return false
//   * a 4xx is treated as "Aegis is healthy, our request is malformed" — no
//     retry, no breaker trip (we'd be punishing Aegis for a config bug)
//   * 5xx / network error → retry; if all attempts fail, trip the breaker
//
// AbortController gives us a per-request timeout — fetch's `signal` aborts the
// in-flight request after `timeoutMs`.

import type { CircuitBreaker } from "./circuit.js";
import type { TraceEvent } from "./trace.js";

const DEFAULT_TIMEOUT_MS = 2_000;
const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_BACKOFF_BASE_MS = 200;
const DEFAULT_BACKOFF_CAP_MS = 2_000;

export type FetchFn = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

export type SleepFn = (ms: number) => Promise<void>;

export interface TransportOpts {
  baseUrl: string;
  apiKey: string;
  breaker: CircuitBreaker;
  timeoutMs?: number;
  maxAttempts?: number;
  backoffBaseMs?: number;
  backoffCapMs?: number;
  /** Override fetch — primarily for tests. Defaults to global fetch. */
  fetchFn?: FetchFn;
  /** Override sleep — primarily for tests. Defaults to setTimeout-Promise. */
  sleepFn?: SleepFn;
}

export class Transport {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly breaker: CircuitBreaker;
  private readonly timeoutMs: number;
  private readonly maxAttempts: number;
  private readonly backoffBaseMs: number;
  private readonly backoffCapMs: number;
  private readonly fetchFn: FetchFn;
  private readonly sleepFn: SleepFn;

  constructor(opts: TransportOpts) {
    const maxAttempts = opts.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
    if (maxAttempts < 1) {
      throw new Error("maxAttempts must be >= 1");
    }
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.apiKey = opts.apiKey;
    this.breaker = opts.breaker;
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxAttempts = maxAttempts;
    this.backoffBaseMs = opts.backoffBaseMs ?? DEFAULT_BACKOFF_BASE_MS;
    this.backoffCapMs = opts.backoffCapMs ?? DEFAULT_BACKOFF_CAP_MS;
    this.fetchFn = opts.fetchFn ?? fetch;
    this.sleepFn =
      opts.sleepFn ?? ((ms: number) => new Promise((r) => setTimeout(r, ms)));
  }

  /** Ship one batch. Returns true iff the backend accepted it. */
  async sendBatch(events: TraceEvent[]): Promise<boolean> {
    if (events.length === 0) {
      return true;
    }
    if (!this.breaker.allow()) {
      return false;
    }

    const body = JSON.stringify({ traces: events.map((e) => e.toPayload()) });
    const url = `${this.baseUrl}/v1/traces/batch`;

    for (let attempt = 0; attempt < this.maxAttempts; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      let resp: Response | null = null;
      try {
        resp = await this.fetchFn(url, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${this.apiKey}`,
            "Content-Type": "application/json",
            "User-Agent": "aegis-sdk-typescript",
          },
          body,
          signal: controller.signal,
        });
      } catch {
        // Network error, abort due to timeout, etc. Treated as retry-able.
      } finally {
        clearTimeout(timer);
      }

      if (resp !== null) {
        if (resp.ok) {
          this.breaker.recordSuccess();
          return true;
        }
        if (resp.status >= 400 && resp.status < 500) {
          // Aegis is up; our request is malformed. Retrying sends the same
          // bad bytes again — record a breaker success and surrender.
          this.breaker.recordSuccess();
          return false;
        }
        // 5xx — fall through to backoff + retry.
      }

      if (attempt < this.maxAttempts - 1) {
        await this.sleepFn(this.backoffFor(attempt));
      }
    }

    this.breaker.recordFailure();
    return false;
  }

  private backoffFor(attempt: number): number {
    return Math.min(this.backoffBaseMs * 2 ** attempt, this.backoffCapMs);
  }
}
