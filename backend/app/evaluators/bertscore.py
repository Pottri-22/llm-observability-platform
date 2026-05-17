"""BERTScore-style semantic-similarity evaluator.

Scores the completion against a labeled `reference_answer` using
sentence-embedding cosine similarity. Activated only when the trace's
metadata carries a non-empty `reference_answer` — without one there's nothing
to compare against, so we skip the row entirely (same skip-row semantics as
the RAGAS evaluator on non-RAG traces).

Naming honesty: this is *not* canonical BERTScore (Zhang et al. 2020), which
computes a token-level F1 over RoBERTa-large contextual embeddings. We use
sentence-level cosine similarity over `all-MiniLM-L6-v2` because:

  1. It's the same library README §6.6 already commits to for the drift
     detector, so the torch+transformers dep cost is amortized across two
     features instead of charged twice.
  2. The MiniLM model is ~80 MB vs RoBERTa-large's ~500 MB — meaningful on
     the Aegis worker image and on free-tier runtime memory.
  3. For the dashboard's portfolio purpose ("score how close the completion
     is to the gold answer, 0-1, higher = better"), sentence-level cosine is
     directionally equivalent and shows the same trend.

The score key is still emitted as `bertscore` for category recognizability —
users reading the dashboard recognize the term. The docstring is where we're
honest about the implementation.

Score direction: 1.0 = identical meaning, 0.0 = unrelated. Negative cosine
values (semantically opposed text) get clamped to 0.0 — in practice these are
vanishingly rare for natural language, and the dashboard's green-for-high
coloring assumes the [0, 1] range.

Activation contract (v0.2 + transitional):
  - `metadata.reference_answer` is a non-empty string → score
  - anything else → skip row

README §6.4 ultimately wants "only on traces inside an eval dataset". When
the eval-datasets feature lands, that signal joins this one (either-or). The
metadata gate ships value now; the dataset gate is additive later.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)


# Cap the text size we ship through the encoder. MiniLM truncates to 256
# tokens anyway, so longer text is wasted work; we still cap by character
# to keep the encode call bounded for pathological 200KB completions.
_MAX_ENCODE_CHARS = 4_000

# Same hard-coded model id as the planned drift detector (§6.6). One model,
# one cache directory inside the worker container — keeps the image size
# growth and cold-start latency to one model, not two.
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Dispatch — does this trace's metadata enable bertscore?
# ---------------------------------------------------------------------------

def is_active(metadata: dict[str, Any] | None) -> bool:
    """True iff `metadata.reference_answer` is a non-empty string.

    Defensive against None metadata, wrong types, whitespace-only references.
    Returning False is the "skip the whole row" signal — same convention
    RAGAS uses on non-RAG traces.
    """
    if not metadata:
        return False
    reference = metadata.get("reference_answer")
    return isinstance(reference, str) and bool(reference.strip())


# ---------------------------------------------------------------------------
# Pure score math — unit tested without loading a real model
# ---------------------------------------------------------------------------

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine of two vectors, clamped to [0, 1].

    Why clamp the floor to 0 instead of remapping [-1,1] → [0,1]: in natural
    language with semantic-quality embeddings the realistic range is roughly
    [0, 1] anyway, and negative values are practically always noise/encoder
    artefacts rather than "this text means the opposite." Clamping keeps the
    intuitive interpretation: 1 = identical meaning, 0 = unrelated.

    The math: dot(a,b) / (||a|| * ||b||). We don't depend on numpy here —
    the vectors are short (384-dim for MiniLM) and Python sum/zip is fast
    enough that pulling numpy just for this is dep overhead we don't need.
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        # Zero vectors don't have a meaningful cosine; treat as "no signal" =
        # lowest score rather than a crash. Should never happen with real
        # encoder output, but the math fallback is cheap.
        return 0.0
    raw = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, raw))


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_ENCODE_CHARS else text[:_MAX_ENCODE_CHARS]


# ---------------------------------------------------------------------------
# Encoder — wrapped behind a callable so tests can inject a stub
# ---------------------------------------------------------------------------

class EncoderFn(Protocol):
    """Anything that maps a list of strings to a list of vectors. The real
    implementation wraps `sentence_transformers.SentenceTransformer.encode`;
    tests pass a deterministic stub."""

    def __call__(self, texts: list[str]) -> list[list[float]]: ...


# Lazy module-level singleton — the model is ~80 MB to load and ~1-2s on
# cold start, so we want exactly one load per worker process, on first eval.
# The worker is preforked (concurrency=2) but each fork loads its own copy;
# that's fine, the model is read-only after load.
_encoder_singleton: EncoderFn | None = None


def _default_encoder() -> EncoderFn:
    """Build (or return cached) sentence-transformers encoder. Imported
    lazily so unit tests can run without the heavy dep installed and so
    test code that injects its own encoder never triggers a model download."""
    global _encoder_singleton
    if _encoder_singleton is not None:
        return _encoder_singleton

    # Local import keeps module import light — pytest collection of
    # test_bertscore.py shouldn't pull torch + transformers off disk.
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME)

    def encode(texts: list[str]) -> list[list[float]]:
        # `convert_to_numpy=False` keeps us in Python lists; cosine math is
        # pure Python anyway, no point materializing a numpy array we
        # immediately serialize back.
        vectors = model.encode(texts, convert_to_numpy=False, normalize_embeddings=False)
        return [list(map(float, v)) for v in vectors]

    _encoder_singleton = encode
    return encode


# ---------------------------------------------------------------------------
# Result + driver
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BertScoreResult:
    """One bertscore eval. `score` is the [0, 1] cosine similarity; reasoning
    is a human-readable summary the dashboard renders next to the bar."""

    score: float
    reasoning: str
    model_name: str


def _bucket(score: float) -> str:
    """Short qualitative label for the reasoning string. Sentence-cosine over
    MiniLM tends to cluster around these zones for natural-language pairs."""
    if score >= 0.85:
        return "near-identical meaning"
    if score >= 0.6:
        return "strongly related"
    if score >= 0.4:
        return "partial overlap"
    if score >= 0.2:
        return "weakly related"
    return "unrelated"


def evaluate(
    trace_completion: str,
    metadata: dict[str, Any] | None,
    *,
    encoder_fn: EncoderFn | None = None,
) -> BertScoreResult | None:
    """Score the completion against `metadata.reference_answer`.

    Returns None to signal "skip row" when the trace doesn't have a reference
    answer to compare against — same convention as RAGAS on non-RAG traces.
    The dispatcher in workers/tasks.py treats None as "write nothing".

    `encoder_fn` is injectable so tests pass a deterministic stub. Production
    uses the lazy-loaded MiniLM singleton; the first call in a worker process
    triggers the model load (~1-2 s), subsequent calls are cheap (~50-100 ms).
    """
    if not is_active(metadata):
        return None

    # is_active already validated shape; cast for the type checker.
    assert metadata is not None  # noqa: S101
    reference = metadata["reference_answer"]
    completion = trace_completion or ""

    fn = encoder_fn or _default_encoder()
    vectors = fn([_truncate(completion), _truncate(reference)])
    if len(vectors) != 2:
        # Encoder contract violation — should never happen. Log and skip
        # rather than crash the worker task.
        log.warning("bertscore.encoder_returned_wrong_shape", extra={"got": len(vectors)})
        return None

    score = cosine_similarity(vectors[0], vectors[1])
    reasoning = f"Semantic similarity to reference: {_bucket(score)} ({score:.2f})."
    return BertScoreResult(score=score, reasoning=reasoning, model_name=MODEL_NAME)
