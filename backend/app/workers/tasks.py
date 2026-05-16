"""Celery task definitions.

`evaluate_trace` is the only registered task today. It:
  1. Fetches the trace's prompt + completion from ClickHouse (scoped to the
     calling project — same isolation contract the read API enforces).
  2. Calls the LLM-as-Judge evaluator (G-Eval), which dispatches `judge_runs`
     parallel-feeling rubric calls and medians the scores.
  3. Writes one `evaluations` row with the result.

The task signature (`trace_id, org_id, project_id`) stays identical to EVAL-A's
placeholder. Future v0.2 evaluators (RAGAS, BERTScore, PII) will fan out from
inside this task, all writing into the same `evaluations` table with their own
`evaluator` value — the ingest enqueue site doesn't change.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import clickhouse_connect
import structlog
from clickhouse_connect.driver.client import Client

from app.config import settings
from app.evaluators import judge
from app.services.cost import compute_cost_usd
from app.workers.celery_app import celery_app

log = structlog.get_logger()

# One ClickHouse client per worker process, lazy-built on first task run.
_ch_client: Client | None = None


def _ch() -> Client:
    global _ch_client
    if _ch_client is None:
        _ch_client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            compress=True,
            connect_timeout=5,
        )
    return _ch_client


_EVAL_COLUMNS = [
    "eval_id",
    "trace_id",
    "org_id",
    "project_id",
    "evaluator",
    "scores",
    "reasoning",
    "judge_model",
    "latency_ms",
    "cost_usd",
    "status",
    "error",
    "created_at",
]


def _fetch_trace_content(
    trace_id: str, project_id: str
) -> tuple[str, str] | None:
    """Read back the prompt + completion to score. Project-scoped — a task that
    somehow received another tenant's trace_id reads zero rows.

    Returns None if no row matches (the trace was never written, or this task
    ran before the insert was visible — possible across replicas, though our
    sync write rules it out for the single-node compose stack)."""
    sql = (
        "SELECT prompt, completion FROM traces "
        "WHERE project_id = {pid:String} AND trace_id = {tid:String} LIMIT 1"
    )
    rows = _ch().query(sql, parameters={"pid": project_id, "tid": trace_id}).result_rows
    if not rows:
        return None
    return rows[0][0], rows[0][1]


def _insert_eval_row(
    *,
    trace_id: str,
    org_id: str,
    project_id: str,
    evaluator: str,
    scores: dict[str, float],
    reasoning: str,
    judge_model: str,
    latency_ms: int,
    cost_usd: float,
    status: str,
    error: str,
) -> None:
    """One INSERT row, columns in the order `_EVAL_COLUMNS` declares."""
    row = (
        str(uuid.uuid4()),
        trace_id,
        org_id,
        project_id,
        evaluator,
        scores,
        reasoning,
        judge_model,
        latency_ms,
        cost_usd,
        status,
        error,
        datetime.now(UTC),
    )
    _ch().insert("evaluations", [row], column_names=_EVAL_COLUMNS)


@celery_app.task(name="evaluate_trace", bind=True, max_retries=3, default_retry_delay=10)
def evaluate_trace(self, trace_id: str, org_id: str, project_id: str) -> None:  # type: ignore[no-untyped-def]
    """Score one trace with every registered evaluator. v0.2 has one: Judge."""
    content = _fetch_trace_content(trace_id, project_id)
    if content is None:
        # The trace doesn't exist for this project. Don't retry (it won't help)
        # and don't write an eval row (would be orphan). Just log it.
        log.warning(
            "eval.trace_missing", trace_id=trace_id, project_id=project_id
        )
        return
    prompt, completion = content

    t0 = time.perf_counter()
    try:
        result = judge.evaluate(prompt, completion)
    except Exception as exc:  # noqa: BLE001 — record + let Celery retry
        log.exception(
            "eval.judge_unhandled", trace_id=trace_id, error=str(exc)
        )
        _insert_eval_row(
            trace_id=trace_id, org_id=org_id, project_id=project_id,
            evaluator="judge", scores={}, reasoning="",
            judge_model="", latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=0.0, status="error", error=repr(exc)[:500],
        )
        # Re-raise to mark the task failed; Celery's retry policy handles it.
        raise

    latency_ms = int((time.perf_counter() - t0) * 1000)
    status = "ok" if result.runs_succeeded > 0 else "error"
    error_text = (
        ""
        if result.runs_succeeded > 0
        else f"all {result.runs_attempted} judge runs failed to parse"
    )
    # Cost is server-side from the catalog: free for Groq, real $ when v0.3
    # swaps to a paid judge. Token counts aren't tracked per attempt today; we
    # estimate cost = 0 for Groq, which is exact. Real token accounting comes
    # with LiteLLM integration in v0.2-late.
    judge_cost = compute_cost_usd(result.judge_model, 0, 0)

    _insert_eval_row(
        trace_id=trace_id,
        org_id=org_id,
        project_id=project_id,
        evaluator="judge",
        scores=result.scores,
        reasoning=result.reasoning,
        judge_model=result.judge_model,
        latency_ms=latency_ms,
        cost_usd=judge_cost,
        status=status,
        error=error_text,
    )
    log.info(
        "eval.written",
        trace_id=trace_id,
        project_id=project_id,
        evaluator="judge",
        runs=f"{result.runs_succeeded}/{result.runs_attempted}",
        scores=result.scores,
    )
