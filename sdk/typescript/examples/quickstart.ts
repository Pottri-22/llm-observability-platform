// Aegis SDK quickstart — and the v0.2 TypeScript end-to-end smoke test.
//
// Same shape as sdk/python/examples/quickstart.py: instrument a real OpenAI
// client pointed at Groq, make one non-streaming and one streaming call, close
// the SDK to flush, then read the traces back via the read API to prove the
// full path:
//
//   aegis.instrument()  →  real LLM call  →  ring buffer  →  setInterval flush
//        →  POST /v1/traces/batch  →  ClickHouse  →  GET /v1/traces
//
// Prerequisites:
//   * Aegis stack up:           docker compose up -d
//   * A seeded API key:         docker compose exec api python -m scripts.seed_demo_tenant
//   * GROQ_API_KEY in env:      already in the repo .env
//
// Run (PowerShell):
//   $env:AEGIS_API_KEY="aegis_dev_..."; $env:GROQ_API_KEY="gsk_..."
//   npx tsx examples/quickstart.ts

import OpenAI from "openai";

import { Aegis } from "../src/index.js";

const AEGIS_BASE_URL = process.env.AEGIS_BASE_URL ?? "http://localhost:8000";
const AEGIS_API_KEY = process.env.AEGIS_API_KEY ?? "";
const GROQ_API_KEY = process.env.GROQ_API_KEY ?? "";
const GROQ_BASE_URL = "https://api.groq.com/openai/v1";
// Free tier; verify periodically at console.groq.com/docs/deprecations.
const GROQ_MODEL = "llama-3.3-70b-versatile";

async function traceCount(headers: Record<string, string>): Promise<number> {
  const resp = await fetch(`${AEGIS_BASE_URL}/v1/traces?limit=1`, { headers });
  if (!resp.ok) throw new Error(`read API ${resp.status}`);
  const body = (await resp.json()) as { meta: { total: number } };
  return body.meta.total;
}

async function main(): Promise<number> {
  if (!AEGIS_API_KEY.startsWith("aegis_")) {
    console.error("AEGIS_API_KEY missing/malformed — seed a tenant and export it first.");
    return 1;
  }
  if (!GROQ_API_KEY.startsWith("gsk_")) {
    console.error("GROQ_API_KEY missing/malformed — expected `gsk_...` (see repo .env).");
    return 1;
  }

  const readHeaders = { Authorization: `Bearer ${AEGIS_API_KEY}` };
  const before = await traceCount(readHeaders);
  console.log(`>>> traces before: ${before}`);

  // --- the two lines a real app adds -------------------------------------
  const aegis = new Aegis({
    apiKey: AEGIS_API_KEY,
    baseUrl: AEGIS_BASE_URL,
    project: "ts-sdk-quickstart",
  });
  const client = aegis.instrument(
    new OpenAI({ apiKey: GROQ_API_KEY, baseURL: GROQ_BASE_URL }),
  );
  // -----------------------------------------------------------------------

  // 1. Non-streaming call
  console.log(">>> non-streaming call...");
  const resp = await client.chat.completions.create({
    model: GROQ_MODEL,
    messages: [{ role: "user", content: "In one sentence, what is observability?" }],
    // `aegis_metadata` is popped by instrument(); never reaches Groq.
    aegis_metadata: { example: "quickstart", kind: "non-stream" },
  } as unknown as Parameters<typeof client.chat.completions.create>[0]);
  // `resp` here is the ChatCompletion (no stream).
  console.log(`    ${("choices" in resp ? resp.choices[0]?.message?.content ?? "" : "").slice(0, 80)}...`);

  // 2. Streaming call — chunks pass straight through; trace finalized on
  //    stream completion with full token + cost rollup.
  console.log(">>> streaming call...");
  const stream = await client.chat.completions.create({
    model: GROQ_MODEL,
    messages: [{ role: "user", content: "Name three LLM failure modes, very briefly." }],
    stream: true,
    aegis_metadata: { example: "quickstart", kind: "stream" },
  } as unknown as Parameters<typeof client.chat.completions.create>[0]);

  const parts: string[] = [];
  // The wrapper is an async iterable of chunks.
  for await (const chunk of stream as unknown as AsyncIterable<{
    choices: Array<{ delta: { content?: string | null } }>;
  }>) {
    const piece = chunk.choices[0]?.delta?.content;
    if (piece) parts.push(piece);
  }
  console.log(`    ${parts.join("").slice(0, 80)}...`);

  // Flush + clean shutdown — guarantees both traces ship before we read back.
  await aegis.close();

  const after = await traceCount(readHeaders);
  console.log(`>>> traces after:  ${after}  (delta ${after - before >= 0 ? "+" : ""}${after - before})`);

  // Newest trace should be our streamed call: groq model, real tokens.
  const listResp = await fetch(`${AEGIS_BASE_URL}/v1/traces?limit=1`, { headers: readHeaders });
  const list = (await listResp.json()) as {
    traces: Array<{ model: string; tokens_in: number; tokens_out: number; cost_usd: number; latency_ms: number }>;
  };
  const latest = list.traces[0]!;
  console.log(
    `>>> latest trace:  model=${latest.model} tokens=${latest.tokens_in}/${latest.tokens_out} ` +
      `cost=$${latest.cost_usd.toFixed(6)} latency=${latest.latency_ms}ms`,
  );

  const ok =
    after - before === 2 &&
    latest.model === `groq/${GROQ_MODEL}` &&
    latest.tokens_out > 0;
  console.log(`\nE2E smoke: ${ok ? "PASS" : "FAIL"}`);
  return ok ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
