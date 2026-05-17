"""Celery task definitions.

`evaluate_trace` is the only registered task. Inside, it fans out across every
registered evaluator — currently `judge` and `pii` — and writes one row per
evaluator into the `evaluations` table. Failures are isolated per-evaluator:
one evaluator throwing must not skip the others, and one evaluator's transient
failure must not duplicate work on retry.

Why no Celery-level retry: each evaluator handles its own resilience (Judge
already does 3 internal rubric calls; PII is pure regex and can't fail). A task
retry would re-run *every* evaluator and double-write the rows that already
succeeded. So `evaluate_trace` always completes "successfully" from Celery's
POV — each evaluator records its own success or failure in its eval row.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect
import structlog
from clickhouse_connect.driver.client import Client

from app.config import settings
from app.evaluators import judge, pii
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
    """Read prompt + completion to score. Project-scoped — a task that somehow
    received another tenant's trace_id reads zero rows."""
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
    row: tuple[Any, ...] = (
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


# ---------------------------------------------------------------------------
# Per-evaluator runners. Each handles its own exceptions and writes its own
# row. None of them raise — the parent task always completes cleanly so Celery
# never retries and never duplicates rows.
# ---------------------------------------------------------------------------

def _run_judge(
    trace_id: str, org_id: str, project_id: str, prompt: str, completion: str
) -> None:
    t0 = time.perf_counter()
    try:
        result = judge.evaluate(prompt, completion)
    except Exception as exc:  # noqa: BLE001
        log.exception("eval.judge_unhandled", trace_id=trace_id, error=str(exc))
        _insert_eval_row(
            trace_id=trace_id, org_id=org_id, project_id=project_id,
            evaluator="judge", scores={}, reasoning="", judge_model="",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=0.0, status="error", error=repr(exc)[:500],
        )
        return

    latency_ms = int((time.perf_counter() - t0) * 1000)
    status = "ok" if result.runs_succeeded > 0 else "error"
    error_text = (
        ""
        if result.runs_succeeded > 0
        else f"all {result.runs_attempted} judge runs failed to parse"
    )
    judge_cost = compute_cost_usd(result.judge_model, 0, 0)

    _insert_eval_row(
        trace_id=trace_id, org_id=org_id, project_id=project_id,
        evaluator="judge", scores=result.scores, reasoning=result.reasoning,
        judge_model=result.judge_model, latency_ms=latency_ms,
        cost_usd=judge_cost, status=status, error=error_text,
    )
    log.info(
        "eval.written",
        trace_id=trace_id, project_id=project_id, evaluator="judge",
        runs=f"{result.runs_succeeded}/{result.runs_attempted}", scores=result.scores,
    )


def _run_pii(
    trace_id: str, org_id: str, project_id: str, prompt: str, completion: str
) -> None:
    t0 = time.perf_counter()
    try:
        result = pii.evaluate(prompt, completion)
    except Exception as exc:  # noqa: BLE001
        log.exception("eval.pii_unhandled", trace_id=trace_id, error=str(exc))
        _insert_eval_row(
            trace_id=trace_id, org_id=org_id, project_id=project_id,
            evaluator="pii", scores={}, reasoning="", judge_model="",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=0.0, status="error", error=repr(exc)[:500],
        )
        return

    latency_ms = int((time.perf_counter() - t0) * 1000)
    reasoning = (
        f"Detected: {', '.join(result.categories)}"
        if result.categories
        else "No PII patterns detected."
    )
    _insert_eval_row(
        trace_id=trace_id, org_id=org_id, project_id=project_id,
        evaluator="pii", scores={"pii_safety": result.score},
        reasoning=reasoning, judge_model="",
        latency_ms=latency_ms, cost_usd=0.0,
        status="ok", error="",
    )
    log.info(
        "eval.written",
        trace_id=trace_id, project_id=project_id, evaluator="pii",
        score=result.score, categories=result.categories,
    )


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------

@celery_app.task(name="evaluate_trace")
def evaluate_trace(trace_id: str, org_id: str, project_id: str) -> None:
    """Score one trace with every registered evaluator."""
    content = _fetch_trace_content(trace_id, project_id)
    if content is None:
        log.warning(
            "eval.trace_missing", trace_id=trace_id, project_id=project_id
        )
        return
    prompt, completion = content

    # Each evaluator is independent: one failure doesn't skip the others.
    _run_judge(trace_id, org_id, project_id, prompt, completion)
    _run_pii(trace_id, org_id, project_id, prompt, completion)
