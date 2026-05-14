"""Week 0 · Block 2 — LiteLLM unified gateway probe.

The point of this script is to prove (in <100 lines of real code) that
``litellm.completion(...)`` lets us call OpenAI, Groq, Anthropic, and Ollama
through one signature. Every probe goes through the same code path; only the
``model`` string (and, for Ollama, ``api_base``) changes.

Why bother, when v0.1 already calls Groq directly? Because v0.2's roadmap
("Multi-provider gateway (LiteLLM)" in README §12) wires this same helper
into the SDK — every Aegis user gets to send traces from any provider through
one consistent shape. This is the prototype for that.

What we do NOT do here:
  - we do not write to the Aegis ingest path (this is a learning sandbox)
  - we do not stream (non-streaming gives one clean end-to-end latency to
    compare across vendors; streaming added value in Block 1, where TTFT was
    the lesson — different lesson today)

Run:
    week0\\.venv\\Scripts\\python.exe week0\\litellm_sandbox.py
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.services.cost import compute_cost_usd, has_known_rate  # noqa: E402

load_dotenv(REPO / ".env")

# Quiet LiteLLM's INFO chatter so the comparison table stays readable.
os.environ.setdefault("LITELLM_LOG", "ERROR")
import litellm  # noqa: E402

litellm.suppress_debug_info = True


PROMPT = "In one sentence, what is the difference between TCP and UDP?"


@dataclass(frozen=True)
class Probe:
    label: str
    # What we pass to litellm.completion(model=...). LiteLLM uses the
    # provider prefix ("groq/", "anthropic/", "ollama_chat/") to route to
    # the right backend; bare names ("gpt-4o-mini") are treated as OpenAI.
    litellm_model: str
    # How the model is keyed inside backend/app/services/cost.py. Diverges
    # from litellm_model on Anthropic, where LiteLLM wants the dated id
    # ("claude-haiku-4-5-20251001") but our catalog stores the short form
    # ("claude-haiku-4-5"). Keeping these separate forces us to acknowledge
    # the mismatch instead of papering over it.
    catalog_key: str
    requires_env: str | None  # None = local provider (Ollama)


PROBES: list[Probe] = [
    Probe("Groq-70B", "groq/llama-3.3-70b-versatile",
          "groq/llama-3.3-70b-versatile", "GROQ_API_KEY"),
    Probe("Groq-8B", "groq/llama-3.1-8b-instant",
          "groq/llama-3.1-8b-instant", "GROQ_API_KEY"),
    Probe("OpenAI", "gpt-4o-mini",
          "gpt-4o-mini", "OPENAI_API_KEY"),
    Probe("Anthropic", "anthropic/claude-haiku-4-5-20251001",
          "claude-haiku-4-5", "ANTHROPIC_API_KEY"),
    # ollama_chat/ (not ollama/) routes through Ollama's /api/chat endpoint,
    # which returns proper prompt_eval_count + eval_count we can map to
    # tokens_in/tokens_out. The plain ollama/ prefix uses /api/generate and
    # often returns 0 for prompt tokens — bad for cost analytics.
    Probe("Ollama", "ollama_chat/llama3.2:3b",
          "ollama/llama3.2:3b", None),
]


def call_one(probe: Probe) -> dict:
    if probe.requires_env and not os.environ.get(probe.requires_env):
        return {"status": "SKIPPED",
                "note": probe.requires_env + " unset in .env"}

    kwargs: dict = {
        "model": probe.litellm_model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": 80,
        "timeout": 30,
    }
    if probe.litellm_model.startswith("ollama"):
        kwargs["api_base"] = "http://localhost:11434"

    t0 = time.perf_counter()
    try:
        resp = litellm.completion(**kwargs)
    except Exception as exc:  # litellm wraps every provider error type
        return {"status": "ERROR",
                "note": type(exc).__name__ + ": " + str(exc)[:90]}
    latency_ms = (time.perf_counter() - t0) * 1000.0

    reply = (resp.choices[0].message.content or "").strip().replace("\n", " ")
    usage = resp.usage
    tokens_in = usage.prompt_tokens or 0
    tokens_out = usage.completion_tokens or 0
    cost_usd = compute_cost_usd(probe.catalog_key, tokens_in, tokens_out)

    return {
        "status": "OK",
        "model": probe.litellm_model,
        "reply": reply,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "in_catalog": has_known_rate(probe.catalog_key) or cost_usd == 0.0,
    }


def _row(label: str, model: str, status: str, cell_in: str, cell_out: str,
         cell_cost: str, cell_lat: str, note: str) -> str:
    return (f"  {label:10s}  {model:44s}  {status:8s}  "
            f"{cell_in:>10s}  {cell_out:>10s}  {cell_cost:>11s}  "
            f"{cell_lat:>9s}  {note}")


def main() -> int:
    print("\nLiteLLM unified gateway probe -- one signature across providers")
    print("=" * 150)
    print(_row("PROVIDER", "LITELLM MODEL", "STATUS", "TOKENS_IN", "TOKENS_OUT",
               "COST_USD", "LATENCY", "REPLY / REASON"))
    print("-" * 150)

    n_ok = 0
    for probe in PROBES:
        r = call_one(probe)
        if r["status"] == "OK":
            n_ok += 1
            print(_row(
                probe.label, r["model"], "OK",
                f"in:{r['tokens_in']}", f"out:{r['tokens_out']}",
                f"${r['cost_usd']:.6f}",
                f"{r['latency_ms']:.0f} ms",
                r["reply"][:55],
            ))
        else:
            print(_row(
                probe.label, probe.litellm_model, r["status"],
                "-", "-", "-", "-", r["note"],
            ))

    print("-" * 150)
    print(f"  {n_ok}/{len(PROBES)} providers responded successfully")
    print()
    return 0 if n_ok else 1


if __name__ == "__main__":
    sys.exit(main())
