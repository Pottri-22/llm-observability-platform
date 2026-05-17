"""RAGAS evaluator — retrieval-aware quality metrics, DIY rubrics.

Activated conditionally on the trace's metadata: only RAG traces (those that
carry `retrieved_chunks` under `aegis_metadata`) get scored here. Non-RAG
traces are skipped entirely — no row written — so the dashboard doesn't show
empty RAGAS cards on traces where the metric doesn't apply.

Three independent 0.0–1.0 metrics, all "higher = better" so the dashboard's
green-for-high color coding stays consistent with Judge and PII:

  - faithfulness     — is the completion grounded in the retrieved chunks?
                       (0 = contradicts / hallucinates, 1 = fully supported)
  - answer_relevance — does the completion address the user's prompt?
                       (0 = off-topic, 1 = directly answers)
  - context_recall   — does the retrieved context cover the reference answer?
                       (0 = missing key info, 1 = fully covered)
                       Only runs when `reference_answer` is supplied.

DIY rubric calls via `llm_service.chat_completion`, not the `ragas` PyPI
package — same reason Judge is DIY: keeps deps light, reuses the single
provider-agnostic LLM gateway, and switching the underlying model stays a
one-line config change.

Median-of-N per metric to stabilize against LLM-judge non-determinism
(`settings.ragas_runs`, default 3, parity with judge).
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.services.llm_service import CompletionResult, chat_completion

log = logging.getLogger(__name__)


# Names listed in the order metrics are evaluated and reported. Stable order
# keeps test assertions, log lines, and dashboard rendering deterministic.
METRIC_FAITHFULNESS = "faithfulness"
METRIC_ANSWER_RELEVANCE = "answer_relevance"
METRIC_CONTEXT_RECALL = "context_recall"

_ALL_METRICS = (METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE, METRIC_CONTEXT_RECALL)

# Same caps as Judge — prompt + completion + chunks can each be quite large
# in real RAG traces, and we don't want to balloon the rubric call.
_MAX_RENDER_CHARS = 8_000
_TRUNC_MARKER = "\n…[truncated]…"


# ---------------------------------------------------------------------------
# Dispatch — which metrics apply to a given trace's metadata?
# ---------------------------------------------------------------------------

def select_metrics(metadata: dict[str, Any] | None) -> list[str]:
    """Return the list of RAGAS metric names that should be computed for the
    trace's metadata, in stable evaluation order. Empty list means "skip the
    whole evaluator" — the dispatcher must not write a row in that case.

    The contract (README §6.4):
      - no `retrieved_chunks`              → [] (not a RAG trace)
      - `retrieved_chunks` only            → [faithfulness, answer_relevance]
      - `retrieved_chunks` + `reference_answer` → also context_recall

    Edge cases handled here so the caller doesn't have to: `metadata` being
    None, `retrieved_chunks` being present but empty/None/wrong-typed, etc.
    """
    if not metadata:
        return []
    chunks = metadata.get("retrieved_chunks")
    if not isinstance(chunks, list) or not chunks:
        return []
    metrics = [METRIC_FAITHFULNESS, METRIC_ANSWER_RELEVANCE]
    reference = metadata.get("reference_answer")
    if isinstance(reference, str) and reference.strip():
        metrics.append(METRIC_CONTEXT_RECALL)
    return metrics


# ---------------------------------------------------------------------------
# Prompt rendering — pure, unit-tested
# ---------------------------------------------------------------------------

def _truncate(text: str) -> str:
    if len(text) <= _MAX_RENDER_CHARS:
        return text
    return text[:_MAX_RENDER_CHARS] + _TRUNC_MARKER


def _render_chunks(chunks: list[str]) -> str:
    """Format the retrieved chunks as a numbered list so the rubric LLM can
    refer to them by index in its reasoning. Each chunk's content is truncated
    individually so one giant chunk can't crowd out the others."""
    body = "\n\n".join(f"[{i + 1}] {_truncate(str(c))}" for i, c in enumerate(chunks))
    return _truncate(body)


def _render_prompt_section(prompt: str) -> str:
    """Same shape-recovery trick as Judge: SDK callers serialize `messages` as
    a JSON array under `prompt`, raw senders send plain text. Try JSON, fall
    back on any failure."""
    if prompt and prompt.lstrip().startswith("["):
        try:
            parsed = json.loads(prompt)
            if isinstance(parsed, list) and all(
                isinstance(m, dict) and "role" in m and "content" in m for m in parsed
            ):
                return "\n".join(f"{m['role']}: {m['content']}" for m in parsed)
        except (json.JSONDecodeError, ValueError):
            pass
    return prompt


_RUBRIC_TAIL = """Return ONLY a single JSON object on one line. No markdown, no commentary, no code fences.
Shape exactly:
{"score": <float 0.0-1.0>, "reasoning": "<one short sentence>"}"""


def build_metric_prompt(
    metric: str,
    *,
    trace_prompt: str,
    trace_completion: str,
    retrieved_chunks: list[str],
    reference_answer: str | None = None,
) -> str:
    """Assemble the rubric prompt for one RAGAS metric.

    Each metric needs a different slice of the trace:
      - faithfulness:     completion + chunks
      - answer_relevance: prompt + completion
      - context_recall:   chunks + reference_answer

    We still render *all* the relevant sections so the LLM has full context —
    the rubric text tells it which axis to score on. Keeps the prompt shape
    uniform and the parser shared.
    """
    rendered_prompt = _truncate(_render_prompt_section(trace_prompt))
    rendered_completion = _truncate(trace_completion or "(empty)")
    rendered_chunks = _render_chunks(retrieved_chunks)
    rendered_reference = _truncate(reference_answer) if reference_answer else ""

    if metric == METRIC_FAITHFULNESS:
        head = (
            "You are evaluating an LLM RAG response on FAITHFULNESS only.\n"
            "Score 0.0-1.0 — is every factual claim in the COMPLETION directly "
            "supported by the RETRIEVED CHUNKS?\n"
            "0 = contradicts the chunks or invents facts not in them; "
            "1 = every claim is grounded in the chunks."
        )
        body = (
            f"--- RETRIEVED CHUNKS ---\n{rendered_chunks}\n\n"
            f"--- COMPLETION ---\n{rendered_completion}\n"
        )
    elif metric == METRIC_ANSWER_RELEVANCE:
        head = (
            "You are evaluating an LLM RAG response on ANSWER RELEVANCE only.\n"
            "Score 0.0-1.0 — does the COMPLETION directly address what the "
            "PROMPT is asking for?\n"
            "0 = off-topic or evades the question; 1 = directly answers it."
        )
        body = (
            f"--- PROMPT ---\n{rendered_prompt}\n\n"
            f"--- COMPLETION ---\n{rendered_completion}\n"
        )
    elif metric == METRIC_CONTEXT_RECALL:
        head = (
            "You are evaluating a RAG retrieval step on CONTEXT RECALL only.\n"
            "Score 0.0-1.0 — do the RETRIEVED CHUNKS contain the information "
            "needed to produce the REFERENCE ANSWER?\n"
            "0 = key info is missing from the chunks; 1 = all needed info is "
            "present (regardless of whether the completion used it)."
        )
        body = (
            f"--- RETRIEVED CHUNKS ---\n{rendered_chunks}\n\n"
            f"--- REFERENCE ANSWER ---\n{rendered_reference}\n"
        )
    else:
        raise ValueError(f"unknown ragas metric: {metric!r}")

    return f"{head}\n\n{_RUBRIC_TAIL}\n\n{body}"


# ---------------------------------------------------------------------------
# Response parsing — pure, unit-tested
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_metric_output(raw: str) -> dict[str, Any] | None:
    """Extract `{score, reasoning}` from a single-metric rubric response.

    Defensive against markdown fences, surrounding prose, and string/int
    scores — same shape as Judge's parser, just one score key instead of
    three. Out-of-range scores are clamped to [0, 1] rather than rejected:
    we'd rather record a slightly-fudged number than drop the whole run."""
    if not raw:
        return None
    match = _JSON_OBJECT_RE.search(raw)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "score" not in parsed:
        return None
    try:
        score = float(parsed["score"])
    except (TypeError, ValueError):
        return None
    reasoning = parsed.get("reasoning", "")
    return {
        "score": max(0.0, min(1.0, score)),
        "reasoning": str(reasoning) if reasoning is not None else "",
    }


# ---------------------------------------------------------------------------
# Median-of-N — pure, unit-tested
# ---------------------------------------------------------------------------

def median_score(runs: list[dict[str, Any]]) -> float | None:
    """Reduce N parsed runs of one metric to a single median score. Returns
    None when `runs` is empty so the driver can mark the metric as failed."""
    if not runs:
        return None
    return statistics.median(float(r["score"]) for r in runs)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

CompletionFn = Callable[..., CompletionResult]


@dataclass(frozen=True)
class RagasResult:
    """One RAGAS dispatch's final output. `scores` keys are a subset of the
    three metric names — only the metrics that actually ran are present.
    `metrics_attempted` lists what `select_metrics` chose; `metrics_succeeded`
    lists those that produced at least one parseable run. Empty `scores` with
    non-empty `metrics_attempted` = total LLM/parse failure, caller writes an
    error-status row."""

    scores: dict[str, float]
    reasoning: str
    metrics_attempted: list[str]
    metrics_succeeded: list[str]
    runs_per_metric: int
    judge_model: str
    per_metric_reasonings: dict[str, str] = field(default_factory=dict)


def _run_one_metric(
    metric: str,
    *,
    trace_prompt: str,
    trace_completion: str,
    retrieved_chunks: list[str],
    reference_answer: str | None,
    completion_fn: CompletionFn,
    model: str,
    runs: int,
) -> tuple[float | None, str]:
    """Run one rubric `runs` times, return (median_score_or_None, reasoning).
    Failures are logged and skipped — the median rides on whatever parsed."""
    rubric = build_metric_prompt(
        metric,
        trace_prompt=trace_prompt,
        trace_completion=trace_completion,
        retrieved_chunks=retrieved_chunks,
        reference_answer=reference_answer,
    )
    parsed_runs: list[dict[str, Any]] = []
    for i in range(runs):
        try:
            result = completion_fn(
                model=model,
                messages=[{"role": "user", "content": rubric}],
                temperature=0.2,  # see judge.evaluate — small diversity helps median-of-N
                max_tokens=200,
                timeout=settings.judge_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 — keep collecting whatever succeeds
            log.warning(
                "ragas.call_failed",
                extra={"metric": metric, "attempt": i + 1, "error": repr(exc)},
            )
            continue
        parsed = parse_metric_output(result.text)
        if parsed is None:
            log.warning(
                "ragas.parse_failed",
                extra={
                    "metric": metric,
                    "attempt": i + 1,
                    "elapsed_ms": result.latency_ms,
                    "raw_head": result.text[:120],
                },
            )
            continue
        parsed_runs.append(parsed)

    median = median_score(parsed_runs)
    # Latest successful run's reasoning, same convention as Judge. Stable
    # across re-runs of the same task; representative because all N runs see
    # the same input.
    reasoning = parsed_runs[-1]["reasoning"] if parsed_runs else ""
    return median, reasoning


def evaluate(
    trace_prompt: str,
    trace_completion: str,
    metadata: dict[str, Any] | None,
    *,
    completion_fn: CompletionFn | None = None,
) -> RagasResult | None:
    """Run RAGAS for the metrics implied by `metadata`. Returns None when no
    metrics apply (non-RAG trace) — the dispatcher uses None as the skip-row
    signal.

    `completion_fn` is injectable for tests so we can stub the LLM call.
    Production callers pass nothing and the default `llm_service.chat_completion`
    is used, dispatching to whichever provider `settings.judge_model` names.
    """
    metrics = select_metrics(metadata)
    if not metrics:
        return None

    # Cast types after select_metrics already validated shape — by the time
    # we're here, retrieved_chunks is a non-empty list[str] for sure.
    assert metadata is not None  # noqa: S101 — invariant from select_metrics
    retrieved_chunks: list[str] = [str(c) for c in metadata["retrieved_chunks"]]
    reference_answer: str | None = (
        metadata.get("reference_answer") if METRIC_CONTEXT_RECALL in metrics else None
    )

    fn = completion_fn or chat_completion
    model = settings.judge_model
    runs = settings.ragas_runs

    scores: dict[str, float] = {}
    per_metric_reasonings: dict[str, str] = {}
    succeeded: list[str] = []
    for metric in metrics:
        median, reasoning = _run_one_metric(
            metric,
            trace_prompt=trace_prompt,
            trace_completion=trace_completion,
            retrieved_chunks=retrieved_chunks,
            reference_answer=reference_answer,
            completion_fn=fn,
            model=model,
            runs=runs,
        )
        if median is None:
            continue
        scores[metric] = median
        per_metric_reasonings[metric] = reasoning
        succeeded.append(metric)

    # Combined reasoning: one short clause per succeeded metric, pipe-separated.
    # The dashboard already renders per-metric score bars from `scores`; this
    # field is the human-readable summary surfaced next to them.
    reasoning_str = " | ".join(
        f"{m}: {per_metric_reasonings[m]}" for m in succeeded if per_metric_reasonings[m]
    )

    return RagasResult(
        scores=scores,
        reasoning=reasoning_str,
        metrics_attempted=metrics,
        metrics_succeeded=succeeded,
        runs_per_metric=runs,
        judge_model=model,
        per_metric_reasonings=per_metric_reasonings,
    )
