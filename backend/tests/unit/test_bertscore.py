"""Unit tests for the BERTScore evaluator's pure logic.

The real `sentence-transformers` encoder is heavy (torch, ~80 MB model) and
covered by e2e — these tests pin the unit-testable core: activation gate,
cosine math + clamping, qualitative bucketing, and driver behaviour with a
stubbed encoder.

Critically: nothing here imports sentence-transformers. The driver tests
inject a deterministic fake `encoder_fn`; the bertscore module's lazy import
of the real encoder lives inside `_default_encoder()` which we never call.
"""

from __future__ import annotations

import pytest

from app.evaluators.bertscore import (
    BertScoreResult,
    _bucket,
    _truncate,
    cosine_similarity,
    evaluate,
    is_active,
)


# --- is_active --------------------------------------------------------------

def test_is_active_none_metadata() -> None:
    assert is_active(None) is False


def test_is_active_empty_dict() -> None:
    assert is_active({}) is False


def test_is_active_missing_reference() -> None:
    # Trace has retrieved_chunks but no reference — RAGAS will run, bertscore
    # has nothing to score against.
    assert is_active({"retrieved_chunks": ["a"]}) is False


def test_is_active_blank_reference() -> None:
    # Whitespace-only references are no-signal — treat as absent.
    assert is_active({"reference_answer": "   "}) is False


def test_is_active_non_string_reference() -> None:
    # Malformed SDK input — defensive skip rather than crash.
    assert is_active({"reference_answer": 42}) is False


def test_is_active_real_reference() -> None:
    assert is_active({"reference_answer": "the gold answer"}) is True


# --- cosine_similarity -----------------------------------------------------

def test_cosine_identical_vectors_is_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_clamps_to_zero() -> None:
    # cos(180°) = -1; we clamp to 0 because the dashboard's green-for-high
    # coloring assumes [0, 1] and negative similarity is rare-to-meaningless
    # for natural-language sentence embeddings.
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == 0.0


def test_cosine_zero_vector_returns_zero() -> None:
    # Degenerate case — encoder shouldn't produce these, but math fallback
    # is cheaper than a runtime assertion.
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="vector length mismatch"):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


def test_cosine_partial_overlap_in_unit_range() -> None:
    # Two non-identical, non-orthogonal vectors should land strictly in (0,1).
    out = cosine_similarity([1.0, 1.0], [1.0, 0.5])
    assert 0.0 < out < 1.0


# --- _bucket ---------------------------------------------------------------

@pytest.mark.parametrize("score,label", [
    (1.0, "near-identical meaning"),
    (0.85, "near-identical meaning"),
    (0.7, "strongly related"),
    (0.5, "partial overlap"),
    (0.3, "weakly related"),
    (0.05, "unrelated"),
    (0.0, "unrelated"),
])
def test_bucket_boundaries(score: float, label: str) -> None:
    assert _bucket(score) == label


# --- _truncate -------------------------------------------------------------

def test_truncate_passthrough_under_cap() -> None:
    assert _truncate("hello") == "hello"


def test_truncate_caps_oversize_text() -> None:
    # Bound is 4_000; MiniLM tokenizes/truncates anyway, but we want a hard
    # ceiling before that to bound encode-call time on pathological input.
    big = "x" * 10_000
    out = _truncate(big)
    assert len(out) == 4_000


# --- evaluate (driver) -----------------------------------------------------

def _stub_encoder(vectors: list[list[float]]):
    """Return an encoder_fn that always emits these vectors in order. Tests
    pass exactly 2 strings to evaluate(), so we expect 2 vectors back."""
    def fn(texts: list[str]) -> list[list[float]]:
        assert len(texts) == 2
        return vectors
    return fn


def test_evaluate_skips_when_no_reference() -> None:
    # Non-RAG trace, no reference → return None → dispatcher writes no row.
    assert evaluate("some answer", metadata=None) is None
    assert evaluate("some answer", metadata={"retrieved_chunks": ["c"]}) is None


def test_evaluate_returns_result_on_active_metadata() -> None:
    fn = _stub_encoder([[1.0, 0.0], [1.0, 0.0]])
    result = evaluate(
        "completion text",
        metadata={"reference_answer": "gold"},
        encoder_fn=fn,
    )
    assert isinstance(result, BertScoreResult)
    assert result.score == pytest.approx(1.0)
    assert "near-identical" in result.reasoning


def test_evaluate_low_similarity_low_score() -> None:
    # Orthogonal vectors → cosine 0 → "unrelated" bucket.
    fn = _stub_encoder([[1.0, 0.0], [0.0, 1.0]])
    result = evaluate(
        "off-topic completion",
        metadata={"reference_answer": "the gold answer"},
        encoder_fn=fn,
    )
    assert result is not None
    assert result.score == pytest.approx(0.0)
    assert "unrelated" in result.reasoning


def test_evaluate_partial_overlap_mid_score() -> None:
    fn = _stub_encoder([[1.0, 1.0, 0.0], [1.0, 0.0, 0.0]])
    result = evaluate(
        "partial answer",
        metadata={"reference_answer": "gold"},
        encoder_fn=fn,
    )
    assert result is not None
    # cos = 1 / sqrt(2) ≈ 0.707 → "strongly related"
    assert 0.6 < result.score < 0.8
    assert "strongly related" in result.reasoning


def test_evaluate_reasoning_includes_numeric_score() -> None:
    # The dashboard renders this reasoning verbatim; it should carry enough
    # signal to be useful next to the score bar.
    fn = _stub_encoder([[1.0, 0.0], [0.5, 0.5]])
    result = evaluate("c", metadata={"reference_answer": "r"}, encoder_fn=fn)
    assert result is not None
    # Reasoning should include the 2-decimal score for readability.
    assert f"{result.score:.2f}" in result.reasoning


def test_evaluate_model_name_in_result() -> None:
    # The dispatcher writes this into the eval row's judge_model column for
    # traceability — which encoder produced which score.
    fn = _stub_encoder([[1.0, 0.0], [1.0, 0.0]])
    result = evaluate("c", metadata={"reference_answer": "r"}, encoder_fn=fn)
    assert result is not None
    assert "MiniLM" in result.model_name


def test_evaluate_skip_signals_distinguishable_from_zero_score() -> None:
    # A skipped trace returns None (no row written). A trace that *was* run
    # but happens to score 0.0 returns a BertScoreResult. Different signals,
    # different dispatcher behaviour.
    skipped = evaluate("c", metadata={})
    assert skipped is None

    fn = _stub_encoder([[1.0, 0.0], [0.0, 1.0]])
    scored = evaluate("c", metadata={"reference_answer": "r"}, encoder_fn=fn)
    assert scored is not None
    assert scored.score == 0.0
