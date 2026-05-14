"""Week 0 · Block 5 — embedding centroid drift with all-MiniLM-L6-v2.

What this teaches:
  1. sentence-transformers/all-MiniLM-L6-v2 — a 384-dim sentence embedding
     model, ~80 MB, runs on CPU in milliseconds. This is the exact model
     Aegis's v0.3 Drift Detector uses (README §6.6) — "local CPU, free".
  2. Centroid drift — embed a batch of prompts, take the mean vector
     (centroid), and compare a new batch's centroid to a frozen baseline
     centroid via cosine distance. One number says "are today's prompts
     drifting from what we tested against?"
  3. Why you need a CONTROL batch. Raw cosine distance has a noise floor:
     two different random samples of the *same* distribution are never
     exactly identical. You can't pick a drift threshold until you've
     measured what "no drift" actually scores.
  4. The math: cosine similarity is magnitude-invariant, which is why
     comparing centroids works even though the centroid of unit vectors
     is itself NOT a unit vector.

Mirrors the v0.3 component in README §6.6 / the "RuPay-on-UPI Diwali drift"
narrative (README line 438): a fintech support bot's prompt mix suddenly
skews toward one topic; the centroid shifts; the detector fires.

Run:
    week0\\.venv\\Scripts\\python.exe week0\\embedding_drift_sandbox.py
First run downloads the model (~80 MB) from HuggingFace into the local
HF cache (%USERPROFILE%\\.cache\\huggingface); subsequent runs are offline.
"""

from __future__ import annotations

import random
import sys
import time

import numpy as np

# Windows console defaults to cp1252, which can't encode the box-drawing chars
# (─) used in section headers. Force UTF-8 so the script runs without a
# PYTHONIOENCODING override. No-op on platforms that already use UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
SEED = 42
BATCH_SIZE = 100  # README week0 table: "centroid for 100 prompts"

# ---------------------------------------------------------------------------
# Synthetic prompt distribution — a fintech support bot.
#
# Each topic is a (template, fillers) pair. We sample prompts by first
# choosing a topic according to a weight distribution, then filling a
# template. BASELINE and CONTROL use the SAME weights (so CONTROL measures
# the noise floor); DRIFTED over-samples one topic (the Diwali RuPay spike).
# ---------------------------------------------------------------------------

_TOPICS: dict[str, list[str]] = {
    "account": [
        "How do I change the email address on my account?",
        "I forgot my login password, how do I reset it?",
        "Why is my account showing as temporarily locked?",
        "How can I update my registered phone number?",
        "Can I have two accounts under the same PAN?",
        "How do I close my savings account permanently?",
    ],
    "payments": [
        "My payment failed but the amount was debited, what now?",
        "How long does an IMPS transfer take to reflect?",
        "Why was my NEFT transfer rejected by the bank?",
        "Can I schedule a recurring payment to my landlord?",
        "What is the daily limit for fund transfers?",
        "How do I get a receipt for a payment I made yesterday?",
    ],
    "cards": [
        "How do I block my debit card if I lost it?",
        "When will my new credit card be delivered?",
        "Why was my card declined at an international merchant?",
        "How do I increase my credit card spending limit?",
        "How can I convert a large purchase into EMI?",
        "What is the annual fee on the premium card?",
    ],
    "loans": [
        "Am I eligible for a personal loan of 5 lakhs?",
        "What documents do I need for a home loan application?",
        "How do I check the status of my loan disbursal?",
        "Can I prepay my loan without a penalty?",
        "What is the current interest rate on car loans?",
        "How do I download my loan repayment schedule?",
    ],
    "rupay_upi": [
        "Why can't I link my RuPay credit card to UPI?",
        "Which merchants accept RuPay credit card on UPI?",
        "Is there a fee for paying with RuPay credit card on UPI?",
        "How do I set a UPI PIN for my RuPay credit card?",
        "My RuPay UPI payment failed at a Diwali sale, why?",
        "Can I use RuPay credit card on UPI for fuel payments?",
        "Why is RuPay UPI showing merchant not compatible?",
        "What is the transaction limit for RuPay credit card on UPI?",
    ],
}

# Baseline / control: an even-ish spread, rupay_upi is a small slice.
_BASELINE_WEIGHTS = {
    "account": 0.25,
    "payments": 0.25,
    "cards": 0.22,
    "loans": 0.20,
    "rupay_upi": 0.08,
}

# Drifted: the Diwali spike — rupay_upi questions go from 8% to ~55% of mix.
_DRIFTED_WEIGHTS = {
    "account": 0.12,
    "payments": 0.13,
    "cards": 0.10,
    "loans": 0.10,
    "rupay_upi": 0.55,
}


def sample_prompts(weights: dict[str, float], n: int, rng: random.Random) -> list[str]:
    """Sample n prompts: pick a topic by weight, then a question from it."""
    topics = list(weights.keys())
    probs = list(weights.values())
    out: list[str] = []
    for _ in range(n):
        topic = rng.choices(topics, weights=probs, k=1)[0]
        out.append(rng.choice(_TOPICS[topic]))
    return out


# ---------------------------------------------------------------------------
# Drift math
# ---------------------------------------------------------------------------

def centroid(embeddings: np.ndarray) -> np.ndarray:
    """Mean vector of a batch. Note: the centroid of unit vectors is itself
    NOT unit-length — its norm shrinks as the batch spreads out. That norm
    is a free signal (batch tightness), and cosine distance below doesn't
    care about it anyway because cosine is magnitude-invariant."""
    return embeddings.mean(axis=0)


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """1 - cosine similarity. 0.0 = identical direction, 1.0 = orthogonal,
    2.0 = opposite. Drift scores live in roughly [0, 0.5] in practice."""
    sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return 1.0 - sim


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hdr(text: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {text}")
    print(f"{'─' * 60}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    rng = random.Random(SEED)

    # ---- build the three batches ----
    _hdr(f"1. Sample 3 batches of {BATCH_SIZE} prompts (seed={SEED})")
    baseline_prompts = sample_prompts(_BASELINE_WEIGHTS, BATCH_SIZE, rng)
    control_prompts = sample_prompts(_BASELINE_WEIGHTS, BATCH_SIZE, rng)
    drifted_prompts = sample_prompts(_DRIFTED_WEIGHTS, BATCH_SIZE, rng)
    print(f"  BASELINE : {BATCH_SIZE} prompts, normal topic mix")
    print(f"  CONTROL  : {BATCH_SIZE} prompts, SAME mix as baseline (noise-floor probe)")
    print(f"  DRIFTED  : {BATCH_SIZE} prompts, rupay_upi spiked 8% → 55% (Diwali scenario)")

    # ---- load model ----
    _hdr(f"2. Load model — {MODEL_NAME}")
    print("  (first run downloads ~80 MB; later runs hit the local HF cache)")
    t0 = time.perf_counter()
    from sentence_transformers import SentenceTransformer  # noqa: E402  (lazy: heavy import)

    model = SentenceTransformer(MODEL_NAME)
    load_ms = (time.perf_counter() - t0) * 1000.0
    # sentence-transformers 5.x renamed get_sentence_embedding_dimension →
    # get_embedding_dimension; fall back for older pins.
    get_dim = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    dim = get_dim()
    print(f"  loaded in {load_ms:.0f} ms · embedding dim = {dim}")

    # ---- embed ----
    _hdr("3. Embed all 3 batches")
    t0 = time.perf_counter()
    # normalize_embeddings=True → each row is a unit vector. Makes the per-
    # prompt vectors directly comparable; the centroid is computed from these.
    emb_baseline = model.encode(baseline_prompts, normalize_embeddings=True)
    emb_control = model.encode(control_prompts, normalize_embeddings=True)
    emb_drifted = model.encode(drifted_prompts, normalize_embeddings=True)
    embed_ms = (time.perf_counter() - t0) * 1000.0
    total = 3 * BATCH_SIZE
    print(f"  embedded {total} prompts in {embed_ms:.0f} ms  "
          f"({total / (embed_ms / 1000):.0f} prompts/sec, CPU)")
    print(f"  emb_baseline shape = {emb_baseline.shape}")

    # ---- centroids ----
    _hdr("4. Centroids — mean vector per batch")
    c_baseline = centroid(emb_baseline)
    c_control = centroid(emb_control)
    c_drifted = centroid(emb_drifted)
    # Centroid norm = batch tightness. Unit-vector inputs, but the mean is
    # shorter than 1.0 — and shorter still when the batch is more spread out.
    print(f"  ‖centroid(BASELINE)‖ = {np.linalg.norm(c_baseline):.4f}")
    print(f"  ‖centroid(CONTROL )‖ = {np.linalg.norm(c_control):.4f}")
    print(f"  ‖centroid(DRIFTED )‖ = {np.linalg.norm(c_drifted):.4f}  "
          f"← tighter (one topic dominates) → larger norm")

    # ---- drift scores ----
    _hdr("5. Drift score — cosine distance vs the BASELINE centroid")
    drift_control = cosine_distance(c_baseline, c_control)
    drift_drifted = cosine_distance(c_baseline, c_drifted)
    print(f"  drift(CONTROL vs BASELINE) = {drift_control:.4f}   ← noise floor")
    print(f"  drift(DRIFTED vs BASELINE) = {drift_drifted:.4f}   ← real shift")
    ratio = drift_drifted / drift_control if drift_control > 0 else float("inf")
    print(f"  ratio = {ratio:.1f}×  (drifted shift is {ratio:.1f}× the noise floor)")

    # A threshold has to sit above the noise floor and below the real shift.
    # Midpoint is a naive pick; v0.3 would calibrate on more control batches.
    threshold = (drift_control + drift_drifted) / 2
    print(f"\n  suggested threshold ≈ {threshold:.4f}  (midpoint — v0.3 calibrates properly)")

    # ---- verdict ----
    _hdr("6. Verdict — does the metric discriminate?")
    control_ok = drift_control < threshold
    drifted_alerts = drift_drifted >= threshold
    print(f"  CONTROL stays under threshold : {control_ok}")
    print(f"  DRIFTED crosses threshold     : {drifted_alerts}")

    if control_ok and drifted_alerts and ratio >= 3.0:
        print("\n  PASS — centroid drift cleanly separates 'same mix' from "
              "'topic spike'. Block 5 target cleared.")
        return 0
    print("\n  FAIL — metric did not separate control from drift as expected.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
