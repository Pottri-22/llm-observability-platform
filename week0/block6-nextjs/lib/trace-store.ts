// Week 0 · Block 6 — server-side in-memory "trace store".
//
// Module-level state persists across requests within a single Next.js server
// process. It is NOT a real database and resets on server restart — it just
// stands in for ClickHouse so the Server Action below has something to mutate.
// In v0.1 this file becomes a ClickHouse query module.

export type Trace = {
  id: string;
  model: string;
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
  latencyMs: number;
  ts: string;
};

const MODELS = ["gpt-4o", "gpt-4o-mini", "claude-sonnet-4-6", "groq/llama-3.3-70b"];

const traces: Trace[] = [
  {
    id: "tr_seed01",
    model: "gpt-4o-mini",
    tokensIn: 120,
    tokensOut: 88,
    costUsd: 0.000078,
    latencyMs: 540,
    ts: new Date().toISOString(),
  },
  {
    id: "tr_seed02",
    model: "claude-sonnet-4-6",
    tokensIn: 310,
    tokensOut: 210,
    costUsd: 0.00234,
    latencyMs: 1320,
    ts: new Date().toISOString(),
  },
];

/** Read side — newest first, matches how the dashboard lists traces. */
export function listTraces(): Trace[] {
  return [...traces].reverse();
}

/** Write side — the Server Action calls this to mutate server state. */
export function addSyntheticTrace(): Trace {
  const model = MODELS[Math.floor(Math.random() * MODELS.length)];
  const tokensIn = 50 + Math.floor(Math.random() * 400);
  const tokensOut = 30 + Math.floor(Math.random() * 300);
  const trace: Trace = {
    id: "tr_" + Math.random().toString(36).slice(2, 10),
    model,
    tokensIn,
    tokensOut,
    costUsd: Number(((tokensIn + tokensOut) * 0.000002).toFixed(6)),
    latencyMs: 200 + Math.floor(Math.random() * 2500),
    ts: new Date().toISOString(),
  };
  traces.push(trace);
  return trace;
}
