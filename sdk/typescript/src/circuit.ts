// Consecutive-failure circuit breaker — stops the SDK from hammering a dead
// Aegis backend. Three states; semantics identical to the Python SDK.
//
//   CLOSED    — normal. Failures accrue toward the threshold.
//   OPEN      — tripped. `allow()` returns false until cooldown elapses.
//   HALF_OPEN — cooldown elapsed. One trial allowed; result resolves the state.
//
// `now` is injectable so tests can drive the cooldown clock without sleeping.

export type CircuitState = "closed" | "open" | "half_open";

export interface CircuitBreakerOpts {
  /** How many consecutive failures trip the breaker. Default 5. */
  failThreshold?: number;
  /** How long it stays OPEN before allowing a HALF_OPEN trial, in ms. Default 30000. */
  resetTimeoutMs?: number;
  /** Override the clock — useful for tests. Default `performance.now`. */
  now?: () => number;
}

export class CircuitBreaker {
  private readonly failThreshold: number;
  private readonly resetTimeoutMs: number;
  private readonly nowFn: () => number;
  private state: CircuitState = "closed";
  private consecutiveFailures = 0;
  private openedAt = 0;

  constructor(opts: CircuitBreakerOpts = {}) {
    const failThreshold = opts.failThreshold ?? 5;
    if (failThreshold < 1) {
      throw new Error("failThreshold must be >= 1");
    }
    this.failThreshold = failThreshold;
    this.resetTimeoutMs = opts.resetTimeoutMs ?? 30_000;
    this.nowFn = opts.now ?? (() => performance.now());
  }

  /** True if a request may proceed.
   *
   *  CLOSED → always. OPEN → only once the cooldown has elapsed, and in that
   *  case the breaker transitions to HALF_OPEN so the caller knows it's a
   *  trial. HALF_OPEN → true (the single trial is in flight).
   */
  allow(): boolean {
    if (this.state === "open") {
      if (this.nowFn() - this.openedAt >= this.resetTimeoutMs) {
        this.state = "half_open";
        return true;
      }
      return false;
    }
    return true; // closed or half_open
  }

  recordSuccess(): void {
    this.consecutiveFailures = 0;
    this.state = "closed";
  }

  recordFailure(): void {
    this.consecutiveFailures++;
    // A failure in HALF_OPEN re-opens immediately regardless of the counter:
    // the trial proved Aegis is still down.
    if (this.state === "half_open" || this.consecutiveFailures >= this.failThreshold) {
      this.state = "open";
      this.openedAt = this.nowFn();
    }
  }

  getState(): CircuitState {
    return this.state;
  }
}
