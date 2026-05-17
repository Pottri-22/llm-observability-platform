"""E2e verification of the RAGAS + BERTScore evaluator dispatch.

Posts four traces covering the activation matrix:
  1. no metadata               → judge + pii          (2 rows)
  2. retrieved_chunks only     → judge + pii + ragas  (3 rows, ragas 2 keys)
  3. reference_answer only     → judge + pii + bertscore  (3 rows)
  4. chunks + reference        → all four evaluators  (4 rows, ragas 3 keys)

Waits for the async eval worker to chew through them, then queries the
evaluations table and asserts the expected row counts and score keys per
trace.

Run from the host venv (memory: backend/.venv-host has httpx).
"""

from __future__ import annotations

import json
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")  # cp1252 quirk on Windows console

API = "http://localhost:8000"
API_KEY = "aegis_dev_w-YfzLH9eKU0HIIWoCUZVUlxHEjTQhcY"
CH = "http://localhost:8123/"  # trailing slash matters — bare host returns 404
CH_AUTH = ("aegis", "aegis_dev_pw")


def ch_query(sql: str) -> list[dict]:
    """Query ClickHouse via HTTP. SQL goes in the POST body (not URL params
    — those return 404). Table names need the `aegis.` qualifier because the
    HTTP session doesn't carry a default database; the connection user's
    default DB isn't picked up via the params hop."""
    full = sql.rstrip().rstrip(";") + " FORMAT JSONEachRow"
    r = httpx.post(CH, content=full, auth=CH_AUTH, timeout=10.0)
    r.raise_for_status()
    return [json.loads(line) for line in r.text.strip().splitlines() if line]


def post_trace(body: dict) -> str:
    r = httpx.post(
        f"{API}/v1/traces",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=body,
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["trace_id"]


def main() -> int:
    print("Posting trace #1: no metadata (non-RAG)")
    t1 = post_trace({
        "model": "gpt-4o-mini",
        "prompt": "What is 2+2?",
        "completion": "2+2 equals 4.",
        "tokens_in": 6,
        "tokens_out": 5,
        "latency_ms": 200,
    })
    print(f"  trace_id = {t1}")

    print("Posting trace #2: retrieved_chunks only (RAG, no reference)")
    t2 = post_trace({
        "model": "gpt-4o-mini",
        "prompt": "What does RAG stand for in LLMs?",
        "completion": "RAG stands for Retrieval-Augmented Generation.",
        "tokens_in": 8,
        "tokens_out": 8,
        "latency_ms": 250,
        "metadata": {
            "retrieved_chunks": [
                "Retrieval-Augmented Generation (RAG) combines a retriever with a generator to ground LLM outputs in external documents.",
                "RAG systems first retrieve top-k passages from a vector store, then condition the LLM on those passages."
            ]
        },
    })
    print(f"  trace_id = {t2}")

    print("Posting trace #3: reference_answer only (labeled, no retrieval)")
    t3 = post_trace({
        "model": "gpt-4o-mini",
        "prompt": "What is the capital of France?",
        "completion": "The capital of France is Paris.",
        "tokens_in": 7,
        "tokens_out": 7,
        "latency_ms": 200,
        "metadata": {
            "reference_answer": "Paris is the capital of France.",
        },
    })
    print(f"  trace_id = {t3}")

    print("Posting trace #4: retrieved_chunks + reference_answer (full RAG + labeled)")
    t4 = post_trace({
        "model": "gpt-4o-mini",
        "prompt": "Who wrote the original Transformer paper?",
        "completion": "The original Transformer paper 'Attention Is All You Need' was authored by Vaswani et al. in 2017.",
        "tokens_in": 9,
        "tokens_out": 18,
        "latency_ms": 280,
        "metadata": {
            "retrieved_chunks": [
                "Vaswani et al. published 'Attention Is All You Need' at NeurIPS 2017, introducing the Transformer architecture.",
                "The paper proposed self-attention as a replacement for recurrence in sequence transduction models."
            ],
            "reference_answer": "Ashish Vaswani et al. wrote 'Attention Is All You Need' in 2017.",
        },
    })
    print(f"  trace_id = {t4}")

    # Worker work per trace: judge (3 Groq calls × ~2s each), ragas (3 × 2-3
    # metrics × ~2s each), bertscore (model load on first call ~30-60s, then
    # ~100 ms). The bertscore first-call download dominates the cold path —
    # pad generously so a fresh worker image has time to fetch MiniLM.
    wait_s = 180
    print(f"\nWaiting {wait_s}s for async eval (judge + pii + ragas + bertscore)...")
    time.sleep(wait_s)

    expectations = [
        (t1, "no-metadata",       {"judge", "pii"},                           None),
        (t2, "chunks-only",       {"judge", "pii", "ragas"},                  {"faithfulness", "answer_relevance"}),
        (t3, "reference-only",    {"judge", "pii", "bertscore"},              None),
        (t4, "chunks+reference",  {"judge", "pii", "ragas", "bertscore"},
         {"faithfulness", "answer_relevance", "context_recall"}),
    ]

    all_ok = True
    for trace_id, label, expected_evals, expected_ragas_keys in expectations:
        print(f"\n--- {label}  ({trace_id}) ---")
        rows = ch_query(
            "SELECT evaluator, scores, status, error, reasoning "
            f"FROM aegis.evaluations WHERE trace_id = '{trace_id}' ORDER BY evaluator"
        )
        found_evals = {r["evaluator"] for r in rows}
        print(f"  evaluators present : {sorted(found_evals)}")
        print(f"  evaluators expected: {sorted(expected_evals)}")
        if found_evals != expected_evals:
            print("  FAIL: evaluator set mismatch")
            all_ok = False
        for row in rows:
            evaluator = row["evaluator"]
            scores = row["scores"] or {}
            status = row["status"]
            error = row["error"]
            reasoning = row["reasoning"]
            print(f"  [{evaluator}] status={status} scores={scores}")
            if reasoning:
                print(f"    reasoning: {reasoning[:200]}")
            if error:
                print(f"    error: {error[:200]}")
            if evaluator == "ragas" and expected_ragas_keys is not None:
                got_keys = set(scores.keys())
                if got_keys != expected_ragas_keys:
                    print(f"    FAIL: ragas score keys {sorted(got_keys)} != expected {sorted(expected_ragas_keys)}")
                    all_ok = False

    print("\n" + ("ALL CHECKS PASSED" if all_ok else "ONE OR MORE CHECKS FAILED"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
