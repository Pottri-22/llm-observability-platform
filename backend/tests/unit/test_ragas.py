"""Unit tests for the RAGAS evaluator's pure logic.

Mirrors test_judge.py — the real LLM call is covered by e2e; here we pin the
unit-testable core: dispatch selection (no row vs. partial vs. full), prompt
rendering for each metric, single-key JSON extraction defensiveness, median
reduction, and driver behaviour with a stubbed completion_fn.
"""

from __future__ import annotations

import json

import pytest

from app.evaluators import ragas
from app.evaluators.ragas import (
    METRIC_ANSWER_RELEVANCE,
    METRIC_CONTEXT_RECALL,
    METRIC_FAITHFULNESS,
    _render_chunks,
    _render_prompt_section,
    build_metric_prompt,
    evaluate,
    median_score,
    parse_metric_output,
    select_metrics,
)
from app.services.llm_service import CompletionResult


# --- select_metrics ---------------------------------------------------------

def test_select_metrics_no_metadata_skips() -> None:
    assert select_metrics(None) == []
    assert select_metrics({}) == []


def test_select_metrics_no_retrieved_chunks_skips() -> None:
    # A non-RAG trace can still carry other metadata fields; ragas must skip.
    assert select_metrics({"user_id": "abc"}) == []


def test_select_metrics_empty_chunks_list_skips() -> None:
    # Explicit empty list — caller sent the key but with no chunks. Still not
    # a RAG trace from RAGAS's POV; skipping avoids a meaningless eval row.
    assert select_metrics({"retrieved_chunks": []}) == []


def test_select_metrics_non_list_chunks_skips() -> None:
    # Malformed metadata — a string under retrieved_chunks. Don't crash, skip.
    assert select_metrics({"retrieved_chunks": "chunk text"}) == []


def test_select_metrics_chunks_only_runs_two_metrics() -> None:
    out = select_metrics({"retrieved_chunks": ["a", "b"]})
    assert out == [METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE]


def test_select_metrics_with_reference_adds_context_recall() -> None:
    out = select_metrics(
        {"retrieved_chunks": ["a"], "reference_answer": "the gold answer"}
    )
    assert out == [METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE, METRIC_CONTEXT_RECALL]


def test_select_metrics_blank_reference_does_not_add_context_recall() -> None:
    # Empty / whitespace-only reference shouldn't gate the third metric on.
    out = select_metrics({"retrieved_chunks": ["a"], "reference_answer": "   "})
    assert out == [METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE]


# --- _render_prompt_section -------------------------------------------------

def test_render_messages_json_as_role_content_lines() -> None:
    raw = '[{"role":"user","content":"what is RAG?"}]'
    assert "user: what is RAG?" in _render_prompt_section(raw)


def test_render_plain_text_passes_through() -> None:
    assert _render_prompt_section("plain question") == "plain question"


# --- _render_chunks ---------------------------------------------------------

def test_render_chunks_numbers_each_chunk() -> None:
    out = _render_chunks(["first", "second", "third"])
    assert "[1] first" in out
    assert "[2] second" in out
    assert "[3] third" in out


def test_render_chunks_truncates_giant_total() -> None:
    # One huge chunk gets per-chunk truncated, then the joined body is also
    # truncated — the hard cap keeps the rubric call bounded.
    big = "x" * 20_000
    out = _render_chunks([big])
    assert "[truncated]" in out
    assert len(out) < 12_000


# --- build_metric_prompt ----------------------------------------------------

def test_build_faithfulness_includes_chunks_and_completion() -> None:
    out = build_metric_prompt(
        METRIC_FAITHFULNESS,
        trace_prompt="ignored",
        trace_completion="the answer",
        retrieved_chunks=["context chunk"],
        reference_answer=None,
    )
    assert "FAITHFULNESS" in out
    assert "RETRIEVED CHUNKS" in out
    assert "context chunk" in out
    assert "the answer" in out


def test_build_answer_relevance_includes_prompt_and_completion() -> None:
    out = build_metric_prompt(
        METRIC_ANSWER_RELEVANCE,
        trace_prompt="what is RAG?",
        trace_completion="RAG stands for...",
        retrieved_chunks=["c1"],
        reference_answer=None,
    )
    assert "ANSWER RELEVANCE" in out
    assert "what is RAG?" in out
    assert "RAG stands for..." in out


def test_build_context_recall_includes_chunks_and_reference() -> None:
    out = build_metric_prompt(
        METRIC_CONTEXT_RECALL,
        trace_prompt="ignored",
        trace_completion="ignored",
        retrieved_chunks=["c1"],
        reference_answer="gold answer",
    )
    assert "CONTEXT RECALL" in out
    assert "REFERENCE ANSWER" in out
    assert "gold answer" in out
    assert "c1" in out


def test_build_metric_prompt_unknown_metric_raises() -> None:
    with pytest.raises(ValueError, match="unknown ragas metric"):
        build_metric_prompt(
            "harmfulness",
            trace_prompt="", trace_completion="",
            retrieved_chunks=["c1"], reference_answer=None,
        )


# --- parse_metric_output ----------------------------------------------------

CLEAN = '{"score": 0.85, "reasoning": "well supported"}'


def test_parse_clean_json() -> None:
    out = parse_metric_output(CLEAN)
    assert out == {"score": 0.85, "reasoning": "well supported"}


def test_parse_strips_markdown_code_fence() -> None:
    raw = "```json\n" + CLEAN + "\n```"
    assert parse_metric_output(raw) is not None


def test_parse_extracts_from_surrounding_prose() -> None:
    raw = "Sure, here you go: " + CLEAN + " — let me know!"
    out = parse_metric_output(raw)
    assert out is not None
    assert out["score"] == 0.85


def test_parse_clamps_out_of_range() -> None:
    out = parse_metric_output('{"score": 1.7, "reasoning": "x"}')
    assert out is not None and out["score"] == 1.0
    out = parse_metric_output('{"score": -0.4, "reasoning": "x"}')
    assert out is not None and out["score"] == 0.0


def test_parse_coerces_int_to_float() -> None:
    out = parse_metric_output('{"score": 1, "reasoning": ""}')
    assert out is not None
    assert isinstance(out["score"], float)
    assert out["score"] == 1.0


def test_parse_returns_none_on_missing_score() -> None:
    assert parse_metric_output('{"reasoning": "no score"}') is None


def test_parse_returns_none_on_non_numeric_score() -> None:
    assert parse_metric_output('{"score": "high", "reasoning": "x"}') is None


def test_parse_returns_none_on_empty_input() -> None:
    assert parse_metric_output("") is None


def test_parse_missing_reasoning_defaults_to_empty_string() -> None:
    out = parse_metric_output('{"score": 0.5}')
    assert out is not None and out["reasoning"] == ""


# --- median_score -----------------------------------------------------------

def test_median_of_three() -> None:
    runs = [{"score": 0.4}, {"score": 0.9}, {"score": 0.7}]
    assert median_score(runs) == 0.7


def test_median_of_two_averages() -> None:
    runs = [{"score": 0.4}, {"score": 0.8}]
    assert median_score(runs) == pytest.approx(0.6)


def test_median_empty_returns_none() -> None:
    assert median_score([]) is None


# --- evaluate (driver) ------------------------------------------------------

def _canned_fn(by_metric: dict[str, str]):
    """Return a fake completion_fn that picks its response based on which
    metric's rubric is in the user message. Each metric's rubric prompt
    contains its uppercase name in the head (FAITHFULNESS / ANSWER RELEVANCE
    / CONTEXT RECALL), so we sniff for that to dispatch."""

    def fn(*, model: str, messages: list[dict[str, str]], **_: object) -> CompletionResult:
        content = messages[0]["content"]
        if "FAITHFULNESS" in content:
            text = by_metric.get(METRIC_FAITHFULNESS, "")
        elif "ANSWER RELEVANCE" in content:
            text = by_metric.get(METRIC_ANSWER_RELEVANCE, "")
        elif "CONTEXT RECALL" in content:
            text = by_metric.get(METRIC_CONTEXT_RECALL, "")
        else:
            text = ""
        return CompletionResult(
            text=text, model=model, tokens_in=0, tokens_out=0, latency_ms=1.0
        )

    return fn


def test_evaluate_returns_none_for_non_rag_trace() -> None:
    # The dispatcher uses None as the "skip the row entirely" signal.
    assert evaluate("q", "a", metadata=None) is None
    assert evaluate("q", "a", metadata={"user_id": "x"}) is None


def test_evaluate_runs_two_metrics_without_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force a single run per metric so the test stays deterministic.
    monkeypatch.setattr(ragas.settings, "ragas_runs", 1)
    fn = _canned_fn({
        METRIC_FAITHFULNESS: '{"score": 0.9, "reasoning": "grounded"}',
        METRIC_ANSWER_RELEVANCE: '{"score": 0.8, "reasoning": "on topic"}',
    })
    result = evaluate(
        "what is RAG?", "RAG combines...",
        metadata={"retrieved_chunks": ["RAG = retrieval-augmented gen"]},
        completion_fn=fn,
    )
    assert result is not None
    assert set(result.scores.keys()) == {METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE}
    assert result.scores[METRIC_FAITHFULNESS] == 0.9
    assert result.scores[METRIC_ANSWER_RELEVANCE] == 0.8
    assert result.metrics_succeeded == [METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE]
    assert "grounded" in result.reasoning
    assert "on topic" in result.reasoning


def test_evaluate_runs_three_metrics_with_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ragas.settings, "ragas_runs", 1)
    fn = _canned_fn({
        METRIC_FAITHFULNESS: '{"score": 1.0, "reasoning": "ok"}',
        METRIC_ANSWER_RELEVANCE: '{"score": 0.9, "reasoning": "ok"}',
        METRIC_CONTEXT_RECALL: '{"score": 0.7, "reasoning": "partial"}',
    })
    result = evaluate(
        "q", "a",
        metadata={"retrieved_chunks": ["c"], "reference_answer": "the gold"},
        completion_fn=fn,
    )
    assert result is not None
    assert set(result.scores.keys()) == {
        METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE, METRIC_CONTEXT_RECALL,
    }
    assert result.scores[METRIC_CONTEXT_RECALL] == 0.7


def test_evaluate_partial_failure_records_succeeded_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # answer_relevance returns junk that won't parse — must not torpedo the
    # whole run; faithfulness still records.
    monkeypatch.setattr(ragas.settings, "ragas_runs", 1)
    fn = _canned_fn({
        METRIC_FAITHFULNESS: '{"score": 0.6, "reasoning": "fine"}',
        METRIC_ANSWER_RELEVANCE: "not valid JSON at all",
    })
    result = evaluate(
        "q", "a",
        metadata={"retrieved_chunks": ["c"]},
        completion_fn=fn,
    )
    assert result is not None
    assert result.metrics_succeeded == [METRIC_FAITHFULNESS]
    assert METRIC_ANSWER_RELEVANCE not in result.scores
    assert METRIC_ANSWER_RELEVANCE in result.metrics_attempted


def test_evaluate_total_failure_returns_empty_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every call returns garbage. Result is non-None (we ran) but scores empty;
    # the dispatcher writes an error-status row in this branch.
    monkeypatch.setattr(ragas.settings, "ragas_runs", 1)
    fn = _canned_fn({
        METRIC_FAITHFULNESS: "junk",
        METRIC_ANSWER_RELEVANCE: "more junk",
    })
    result = evaluate(
        "q", "a",
        metadata={"retrieved_chunks": ["c"]},
        completion_fn=fn,
    )
    assert result is not None
    assert result.scores == {}
    assert result.metrics_succeeded == []


def test_evaluate_swallows_llm_call_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Network blew up mid-rubric — the driver logs and treats it as a failed
    # run, doesn't propagate, matching the per-evaluator fault-isolation
    # contract in tasks.py.
    monkeypatch.setattr(ragas.settings, "ragas_runs", 2)

    call_count = {"n": 0}

    def flaky_fn(**_: object) -> CompletionResult:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("groq 503")
        return CompletionResult(
            text='{"score": 0.5, "reasoning": "ok"}',
            model="m", tokens_in=0, tokens_out=0, latency_ms=1.0,
        )

    result = evaluate(
        "q", "a",
        metadata={"retrieved_chunks": ["c"]},
        completion_fn=flaky_fn,
    )
    assert result is not None
    # First call raised, second succeeded — median is the single 0.5.
    # But this only covers one metric's run sequence; with ragas_runs=2 we
    # get 2 calls per metric × 2 metrics = 4 calls total. The first metric
    # has one fail + one ok → median 0.5. The second metric has 2 oks → 0.5.
    assert METRIC_FAITHFULNESS in result.scores
    assert result.scores[METRIC_FAITHFULNESS] == 0.5


def test_evaluate_handles_messages_json_in_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SDK-shaped prompts (a JSON array of {role, content}) must render
    # readably in the rubric — covered in the unit test for _render_prompt_section,
    # this is the integration of that with the driver.
    monkeypatch.setattr(ragas.settings, "ragas_runs", 1)
    captured: dict[str, str] = {}

    def capture_fn(*, model: str, messages: list[dict[str, str]], **_: object) -> CompletionResult:
        captured["rubric"] = messages[0]["content"]
        return CompletionResult(
            text='{"score": 1.0, "reasoning": "ok"}',
            model=model, tokens_in=0, tokens_out=0, latency_ms=1.0,
        )

    sdk_prompt = json.dumps([{"role": "user", "content": "what is RAG?"}])
    evaluate(
        sdk_prompt, "answer",
        metadata={"retrieved_chunks": ["c"]},
        completion_fn=capture_fn,
    )
    # The rubric for answer_relevance should render the role:content line,
    # not the raw JSON array.
    assert "user: what is RAG?" in captured["rubric"]
