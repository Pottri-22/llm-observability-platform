"""Aegis SDK quickstart — and the v0.1 end-to-end smoke test.

This is both the canonical "drop two lines in your app" example and a real
round-trip check: it runs the actual SDK against a live Aegis backend and a real
LLM provider (Groq's free tier), then reads the traces back out to prove the whole
path works:

    aegis.instrument()  ->  real LLM call  ->  ring buffer  ->  flush thread
        ->  POST /v1/traces/batch  ->  ClickHouse  ->  GET /v1/traces

Prerequisites:
  * Aegis stack running:   docker compose up -d
  * A seeded API key:      docker compose exec api python -m scripts.seed_demo_tenant
  * Groq free-tier key:    already in the repo .env as GROQ_API_KEY

Run (PowerShell):
    $env:AEGIS_API_KEY="aegis_dev_..."; $env:GROQ_API_KEY="gsk_..."
    .venv\\Scripts\\python.exe examples\\quickstart.py
"""

from __future__ import annotations

import os
import sys

import httpx
from openai import OpenAI

from aegis_sdk import Aegis

# Windows consoles default to cp1252 and choke on the ✓/✗ this script prints.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AEGIS_BASE_URL = os.environ.get("AEGIS_BASE_URL", "http://localhost:8000")
AEGIS_API_KEY = os.environ.get("AEGIS_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.3-70b-versatile"  # free tier; verify at console.groq.com/docs/deprecations


def _trace_count(http: httpx.Client) -> int:
    """Total traces visible to this API key, via the read API."""
    resp = http.get("/v1/traces", params={"limit": 1})
    resp.raise_for_status()
    return int(resp.json()["meta"]["total"])


def main() -> int:
    if not AEGIS_API_KEY.startswith("aegis_"):
        sys.exit("AEGIS_API_KEY missing/malformed — seed a tenant and export it first.")
    if not GROQ_API_KEY.startswith("gsk_"):
        sys.exit("GROQ_API_KEY missing/malformed — expected `gsk_...` (see repo .env).")

    read_api = httpx.Client(
        base_url=AEGIS_BASE_URL,
        headers={"Authorization": f"Bearer {AEGIS_API_KEY}"},
        timeout=10.0,
    )
    before = _trace_count(read_api)
    print(f">>> traces before: {before}")

    # --- the two lines a real app adds -------------------------------------
    aegis = Aegis(api_key=AEGIS_API_KEY, base_url=AEGIS_BASE_URL, project="sdk-quickstart")
    client = aegis.instrument(OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL))
    # -----------------------------------------------------------------------

    # 1. a plain non-streaming call
    print(">>> non-streaming call...")
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": "In one sentence, what is observability?"}],
        aegis_metadata={"example": "quickstart", "kind": "non-stream"},
    )
    print(f"    {resp.choices[0].message.content[:80]}...")

    # 2. a streaming call — chunks pass straight through; the trace is finalized
    #    after the stream completes, with full token + cost rollup
    print(">>> streaming call...")
    stream = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": "Name three LLM failure modes, very briefly."}],
        stream=True,
        aegis_metadata={"example": "quickstart", "kind": "stream"},
    )
    streamed = "".join(
        chunk.choices[0].delta.content
        for chunk in stream
        if chunk.choices and chunk.choices[0].delta.content
    )
    print(f"    {streamed[:80]}...")

    # flush + clean shutdown — guarantees both traces are shipped before we read back
    aegis.close()

    after = _trace_count(read_api)
    print(f">>> traces after:  {after}  (delta {after - before:+d})")

    # newest trace should be our streamed call: groq model, real tokens, real completion
    latest = read_api.get("/v1/traces", params={"limit": 1}).json()["traces"][0]
    print(f">>> latest trace:  model={latest['model']} "
          f"tokens={latest['tokens_in']}/{latest['tokens_out']} "
          f"cost=${latest['cost_usd']:.6f} latency={latest['latency_ms']}ms")

    read_api.close()

    ok = (
        after - before == 2
        and latest["model"] == f"groq/{GROQ_MODEL}"
        and latest["tokens_out"] > 0
    )
    print("\nE2E smoke:", "PASS ✓" if ok else "FAIL ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
