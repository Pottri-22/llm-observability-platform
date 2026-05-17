import { beforeEach, describe, expect, it } from "vitest";

import { CircuitBreaker } from "../src/circuit.js";

// A controllable clock instead of monkeypatching globals — same idea as the
// Python tests' FakeClock, but cleaner because the breaker accepts `now` as a
// constructor option.
class Clock {
  t = 1_000;
  now = (): number => this.t;
  advance(dt: number): void {
    this.t += dt;
  }
}

describe("CircuitBreaker", () => {
  let clock: Clock;
  beforeEach(() => {
    clock = new Clock();
  });

  it("starts CLOSED and allows requests", () => {
    const cb = new CircuitBreaker({ failThreshold: 3, now: clock.now });
    expect(cb.getState()).toBe("closed");
    expect(cb.allow()).toBe(true);
  });

  it("opens only when consecutive failures hit the threshold", () => {
    const cb = new CircuitBreaker({ failThreshold: 3, resetTimeoutMs: 30_000, now: clock.now });
    cb.recordFailure();
    cb.recordFailure();
    expect(cb.getState()).toBe("closed"); // two failures, threshold not hit
    cb.recordFailure();
    expect(cb.getState()).toBe("open");
    expect(cb.allow()).toBe(false);
  });

  it("transitions to HALF_OPEN after the cooldown elapses", () => {
    const cb = new CircuitBreaker({ failThreshold: 1, resetTimeoutMs: 30_000, now: clock.now });
    cb.recordFailure(); // → OPEN
    expect(cb.allow()).toBe(false);

    clock.advance(29_000);
    expect(cb.allow()).toBe(false); // cooldown not yet elapsed

    clock.advance(2_000); // now past 30s
    expect(cb.allow()).toBe(true);
    expect(cb.getState()).toBe("half_open");
  });

  it("a success in HALF_OPEN closes the breaker", () => {
    const cb = new CircuitBreaker({ failThreshold: 1, resetTimeoutMs: 10_000, now: clock.now });
    cb.recordFailure();
    clock.advance(11_000);
    cb.allow(); // → HALF_OPEN
    cb.recordSuccess();
    expect(cb.getState()).toBe("closed");
  });

  it("a failure in HALF_OPEN re-opens immediately and resets the cooldown clock", () => {
    const cb = new CircuitBreaker({ failThreshold: 1, resetTimeoutMs: 10_000, now: clock.now });
    cb.recordFailure();
    clock.advance(11_000);
    cb.allow(); // → HALF_OPEN
    cb.recordFailure(); // trial failed
    expect(cb.getState()).toBe("open");
    expect(cb.allow()).toBe(false); // cooldown reset
  });

  it("a success resets the consecutive-failure counter", () => {
    const cb = new CircuitBreaker({ failThreshold: 3, resetTimeoutMs: 30_000, now: clock.now });
    cb.recordFailure();
    cb.recordFailure();
    cb.recordSuccess();
    cb.recordFailure();
    cb.recordFailure();
    expect(cb.getState()).toBe("closed"); // only 2 since the reset
  });
});
