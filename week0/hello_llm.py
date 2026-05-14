"""Week 0 · Block 1 — OpenAI SDK basics, dogfooded against Groq's free tier.

What this teaches:
  1. The OpenAI Python SDK works against any OpenAI-compatible endpoint —
     Groq exposes one at https://api.groq.com/openai/v1, so we get a real
     LLM call for ₹0 while learning the same SDK Aegis will instrument later.
  2. Streaming + usage. With stream=True the response arrives chunk-by-chunk;
     stream_options={"include_usage": True} asks the server to send a final
     chunk that carries prompt_tokens / completion_tokens. That final chunk
     is the foundation of every "tokens/sec" and "$/conversation" metric
     Aegis will graph in v0.1+.
  3. Cost = tokens × per-model rate. We import the *same* compute_cost_usd
     function the backend uses, so this script is a tiny version of the
     ingest path: real call → real usage → real cost.

Run:
    python -m pip install --user openai python-dotenv
    python week0/hello_llm.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# Make backend/app importable so we can dogfood Aegis's own cost catalog.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.services.cost import compute_cost_usd  # noqa: E402

load_dotenv(REPO / ".env")

GROQ_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_KEY or not GROQ_KEY.startswith("gsk_"):
    sys.exit("GROQ_API_KEY missing or malformed in .env (expected `gsk_...`).")

# Groq is OpenAI-compatible — same SDK, different base_url.
client = OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1")

GROQ_MODEL = "llama-3.3-70b-versatile"  # free tier; verify at console.groq.com/docs/deprecations
PROMPT = "In one sentence, what is the difference between TCP and UDP?"


def main() -> int:
    print(f"\n>>> Calling Groq ({GROQ_MODEL}) with stream=True\n")
    print("-" * 60)

    t0 = time.perf_counter()
    stream = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
        stream_options={"include_usage": True},  # ← final chunk carries usage
    )

    first_token_at: float | None = None
    last_token_at: float | None = None
    finish_reason: str | None = None
    usage = None

    for chunk in stream:
        # Token chunks: print delta as it arrives, no buffering.
        if chunk.choices and chunk.choices[0].delta.content:
            now = time.perf_counter()
            if first_token_at is None:
                first_token_at = now
            last_token_at = now
            print(chunk.choices[0].delta.content, end="", flush=True)
        # finish_reason lands on the terminating choice chunk (its delta may be
        # empty). stop = clean end · length = hit max_tokens · tool_calls =
        # model wants a tool · content_filter = blocked. Aegis traces this too.
        if chunk.choices and chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason
        # Final chunk has empty `choices` and a populated `usage` field.
        if chunk.usage is not None:
            usage = chunk.usage

    total_elapsed = time.perf_counter() - t0
    ttft = (first_token_at - t0) if first_token_at else float("nan")
    # Throughput is measured over the *generation window* (first→last token),
    # NOT total elapsed — folding TTFT in would understate the model's real
    # token-generation speed. Two different questions, two different metrics.
    gen_window = (
        last_token_at - first_token_at
        if first_token_at and last_token_at and last_token_at > first_token_at
        else float("nan")
    )

    print("\n" + "-" * 60)

    if usage is None:
        print("WARNING: no usage chunk received — server didn't honor stream_options.")
        return 1

    # cost.py keys Groq models with the `groq/` prefix (LiteLLM convention used in v0.2).
    cost_actual = compute_cost_usd(f"groq/{GROQ_MODEL}", usage.prompt_tokens, usage.completion_tokens)
    cost_if_gpt4o_mini = compute_cost_usd(
        "gpt-4o-mini", usage.prompt_tokens, usage.completion_tokens
    )
    cost_if_claude_sonnet = compute_cost_usd(
        "claude-sonnet-4-6", usage.prompt_tokens, usage.completion_tokens
    )

    # tok/s over the generation window; nan if we never saw two timed tokens.
    tokens_per_sec = (
        usage.completion_tokens / gen_window if gen_window == gen_window else float("nan")
    )

    print(f"\nTokens   in:  {usage.prompt_tokens}")
    print(f"Tokens   out: {usage.completion_tokens}")
    print(f"Tokens   total: {usage.total_tokens}")
    print(f"\nLatency  TTFT:        {ttft * 1000:7.0f} ms")
    print(f"Latency  end-to-end:  {total_elapsed * 1000:7.0f} ms")
    print(f"Throughput:           {tokens_per_sec:7.1f} tok/s   (generation window)")
    print(f"\nFinish reason: {finish_reason}")
    print(f"\nCost on Groq {GROQ_MODEL}:  $ {cost_actual:.6f}   (free tier)")
    print(f"Cost if gpt-4o-mini:           $ {cost_if_gpt4o_mini:.6f}")
    print(f"Cost if claude-sonnet-4-6:     $ {cost_if_claude_sonnet:.6f}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
