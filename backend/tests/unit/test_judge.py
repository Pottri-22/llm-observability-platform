"""Unit tests for the LLM-as-Judge evaluator's pure logic.

The actual LLM call (`judge.evaluate`) is covered by the e2e against live
Groq — these tests pin the unit-testable core: prompt rendering, JSON
extraction defensiveness, score clamping, and the median-of-N reduction.
"""

from __future__ import annotations

import pytest

from app.evaluators.judge import (
    _render_prompt_section,
    build_judge_prompt,
    median_scores,
    parse_judge_output,
)


# --- _render_prompt_section -------------------------------------------------

def test_render_messages_json_as_role_content_lines() -> None:
    raw = '[{"role":"system","content":"be terse"},{"role":"user","content":"hi"}]'
    out = _render_prompt_section(raw)
    assert "system: be terse" in out
    assert "user: hi" in out


def test_render_plain_text_passes_through() -> None:
    assert _render_prompt_section("What is TCP?") == "What is TCP?"


def test_render_malformed_json_falls_back_to_raw() -> None:
    # Looks JSON-ish but isn't — must not crash, must not lose the content.
    raw = "[not json"
    assert _render_prompt_section(raw) == raw


def test_render_json_with_wrong_shape_falls_back() -> None:
    # Valid JSON array, but elements aren't {role, content} — render as raw.
    raw = '[1, 2, 3]'
    assert _render_prompt_section(raw) == raw


# --- build_judge_prompt -----------------------------------------------------

def test_build_judge_prompt_includes_both_sections() -> None:
    out = build_judge_prompt("the prompt", "the completion")
    assert "--- PROMPT ---" in out
    assert "--- COMPLETION ---" in out
    assert "the prompt" in out
    assert "the completion" in out


def test_build_judge_prompt_truncates_oversized_content() -> None:
    big = "x" * 20_000
    out = build_judge_prompt(big, "ok")
    assert "[truncated]" in out
    # Hard cap: 8000 chars + marker + the rest of the rubric scaffolding.
    assert len(out) < 12_000


def test_build_judge_prompt_handles_empty_completion() -> None:
    out = build_judge_prompt("ask something", "")
    assert "(empty)" in out  # explicit placeholder, not a confusing blank


# --- parse_judge_output -----------------------------------------------------

CLEAN_JSON = '{"accuracy": 0.9, "completeness": 0.8, "safety": 1.0, "reasoning": "fine"}'


def test_parse_clean_json() -> None:
    parsed = parse_judge_output(CLEAN_JSON)
    assert parsed == {"accuracy": 0.9, "completeness": 0.8, "safety": 1.0, "reasoning": "fine"}


def test_parse_strips_markdown_code_fence() -> None:
    raw = "```json\n" + CLEAN_JSON + "\n```"
    assert parse_judge_output(raw) is not None


def test_parse_extracts_from_surrounding_prose() -> None:
    raw = "Here is the JSON you asked for: " + CLEAN_JSON + " Hope this helps!"
    parsed = parse_judge_output(raw)
    assert parsed is not None
    assert parsed["accuracy"] == 0.9


def test_parse_clamps_out_of_range_scores() -> None:
    raw = '{"accuracy": 1.5, "completeness": -0.2, "safety": 0.5, "reasoning": "x"}'
    parsed = parse_judge_output(raw)
    assert parsed is not None
    assert parsed["accuracy"] == 1.0  # clamped down
    assert parsed["completeness"] == 0.0  # clamped up


def test_parse_coerces_int_scores_to_float() -> None:
    raw = '{"accuracy": 1, "completeness": 0, "safety": 1, "reasoning": ""}'
    parsed = parse_judge_output(raw)
    assert parsed is not None
    assert parsed["accuracy"] == 1.0
    assert isinstance(parsed["accuracy"], float)


def test_parse_returns_none_on_missing_dimension() -> None:
    # Drop `safety` — run is unusable, must not silently default to 0.
    raw = '{"accuracy": 1.0, "completeness": 1.0, "reasoning": "x"}'
    assert parse_judge_output(raw) is None


def test_parse_returns_none_on_non_numeric_score() -> None:
    raw = '{"accuracy": "yes", "completeness": 1.0, "safety": 1.0, "reasoning": "x"}'
    assert parse_judge_output(raw) is None


def test_parse_returns_none_on_empty_input() -> None:
    assert parse_judge_output("") is None


def test_parse_returns_none_when_no_json_object_found() -> None:
    assert parse_judge_output("totally not json") is None


def test_parse_missing_reasoning_defaults_to_empty_string() -> None:
    raw = '{"accuracy": 0.5, "completeness": 0.5, "safety": 0.5}'
    parsed = parse_judge_output(raw)
    assert parsed is not None
    assert parsed["reasoning"] == ""


# --- median_scores ---------------------------------------------------------

def _run(a: float, c: float, s: float) -> dict[str, object]:
    return {"accuracy": a, "completeness": c, "safety": s, "reasoning": ""}


def test_median_of_three_runs() -> None:
    runs = [_run(0.5, 0.6, 0.7), _run(0.9, 0.5, 0.9), _run(0.7, 0.7, 0.8)]
    medians = median_scores(runs)
    assert medians == {"accuracy": 0.7, "completeness": 0.6, "safety": 0.8}


def test_median_with_partial_runs_uses_what_we_have() -> None:
    # Two-run case (one of three failed to parse): median is the avg of the two.
    medians = median_scores([_run(0.4, 0.5, 0.6), _run(0.8, 0.7, 0.9)])
    assert medians is not None
    assert medians["accuracy"] == pytest.approx(0.6)


def test_median_with_no_runs_returns_none() -> None:
    # Caller writes an error-status eval row when this happens.
    assert median_scores([]) is None
