"""Celery task definitions.

`evaluate_trace` is the only registered task. Inside, it fans out across every
registered evaluator — currently `judge`, `pii`, `ragas`, and `bertscore` —
and writes one row per evaluator into the `evaluations` table. RAGAS and
BERTScore are conditional: RAGAS only writes a row when the trace's metadata
carries `retrieved_chunks`; BERTScore only when it carries `reference_answer`.
Failures are isolated per-evaluator: one evaluator throwing must not skip the
others, and one evaluator's transient failure must not duplicate work on
retry.

Why no Celery-level retry: each evaluator handles its own resilience (Judge
already does 3 internal rubric calls; PII is pure regex and can't fail). A task
retry would re-run *every* evaluator and double-write the rows that already
succeeded. So `evaluate_trace` always completes "successfully" from Celery's
POV — each evaluator records its own success or failure in its eval row.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect
import structlog
from clickhouse_connect.driver.client import Client

from app.config import settings
from app.evaluators import bertscore, judge, pii, ragas
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
) -> tuple[str, str, dict[str, Any]] | None:
    """Read prompt + completion + metadata to score. Project-scoped — a task
    that somehow received another tenant's trace_id reads zero rows.

    `metadata` is stored as a JSON string in ClickHouse (see trace_writer:62),
    so we decode it here. Malformed JSON or non-dict shapes (legacy rows,
    direct inserts) degrade gracefully to an empty dict; RAGAS reads from
    `metadata.retrieved_chunks` which is just absent in that case, so it
    correctly skips itself."""
    sql = (
        "SELECT prompt, completion, metadata FROM traces "
        "WHERE project_id = {pid:String} AND trace_id = {tid:String} LIMIT 1"
    )
    rows = _ch().query(sql, parameters={"pid": project_id, "tid": trace_id}).result_rows
    if not rows:
        return None
    prompt, completion, metadata_raw = rows[0][0], rows[0][1], rows[0][2]
    metadata: dict[str, Any] = {}
    if metadata_raw:
        try:
            decoded = json.loads(metadata_raw)
            if isinstance(decoded, dict):
                metadata = decoded
        except (json.JSONDecodeError, TypeError):
            log.warning(
                "eval.metadata_decode_failed",
                trace_id=trace_id, project_id=project_id,
            )
    return prompt, completion, metadata


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


def _run_ragas(
    trace_id: str,
    org_id: str,
    project_id: str,
    prompt: str,
    completion: str,
    metadata: dict[str, Any],
) -> None:
    """RAGAS is *conditional*: only RAG traces (those carrying retrieved_chunks
    in metadata) get a row at all. ragas.evaluate returns None to signal "skip
    this trace entirely" — no row, not even a status=skipped one, so the
    dashboard doesn't show empty RAGAS cards for non-RAG traces."""
    t0 = time.perf_counter()
    try:
        result = ragas.evaluate(prompt, completion, metadata)
    except Exception as exc:  # noqa: BLE001
        log.exception("eval.ragas_unhandled", trace_id=trace_id, error=str(exc))
        _insert_eval_row(
            trace_id=trace_id, org_id=org_id, project_id=project_id,
            evaluator="ragas", scores={}, reasoning="", judge_model="",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=0.0, status="error", error=repr(exc)[:500],
        )
        return

    if result is None:
        # Not a RAG trace — no metric applies. Leave no row.
        return

    latency_ms = int((time.perf_counter() - t0) * 1000)
    status = "ok" if result.metrics_succeeded else "error"
    error_text = (
        ""
        if result.metrics_succeeded
        else f"all {len(result.metrics_attempted)} ragas metrics failed to parse"
    )
    # Cost-tracked the same way as judge: per-call cost is provider-dependent
    # and the eval row's token counters aren't broken out per-call yet, so we
    # use the model-level lookup with zero tokens (effectively $0 on Groq).
    ragas_cost = compute_cost_usd(result.judge_model, 0, 0)

    _insert_eval_row(
        trace_id=trace_id, org_id=org_id, project_id=project_id,
        evaluator="ragas", scores=result.scores, reasoning=result.reasoning,
        judge_model=result.judge_model, latency_ms=latency_ms,
        cost_usd=ragas_cost, status=status, error=error_text,
    )
    log.info(
        "eval.written",
        trace_id=trace_id, project_id=project_id, evaluator="ragas",
        metrics=f"{len(result.metrics_succeeded)}/{len(result.metrics_attempted)}",
        scores=result.scores,
    )


def _run_bertscore(
    trace_id: str,
    org_id: str,
    project_id: str,
    completion: str,
    metadata: dict[str, Any],
) -> None:
    """BERTScore is *conditional*: only traces carrying `reference_answer` in
    metadata get a row. bertscore.evaluate returns None to signal "skip this
    trace entirely" — no row at all, mirroring the RAGAS contract."""
    t0 = time.perf_counter()
    try:
        result = bertscore.evaluate(completion, metadata)
    except Exception as exc:  # noqa: BLE001
        log.exception("eval.bertscore_unhandled", trace_id=trace_id, error=str(exc))
        _insert_eval_row(
            trace_id=trace_id, org_id=org_id, project_id=project_id,
            evaluator="bertscore", scores={}, reasoning="", judge_model="",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=0.0, status="error", error=repr(exc)[:500],
        )
        return

    if result is None:
        # No reference_answer → nothing to score against. Leave no row.
        return

    latency_ms = int((time.perf_counter() - t0) * 1000)
    _insert_eval_row(
        trace_id=trace_id, org_id=org_id, project_id=project_id,
        evaluator="bertscore", scores={"bertscore": result.score},
        reasoning=result.reasoning,
        # `judge_model` column repurposed for evaluator-model traceability —
        # for bertscore this is the sentence-transformer id, not an LLM.
        judge_model=result.model_name,
        latency_ms=latency_ms, cost_usd=0.0,
        status="ok", error="",
    )
    log.info(
        "eval.written",
        trace_id=trace_id, project_id=project_id, evaluator="bertscore",
        score=result.score,
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
    prompt, completion, metadata = content

    # Each evaluator is independent: one failure doesn't skip the others.
    _run_judge(trace_id, org_id, project_id, prompt, completion)
    _run_pii(trace_id, org_id, project_id, prompt, completion)
    _run_ragas(trace_id, org_id, project_id, prompt, completion, metadata)
    _run_bertscore(trace_id, org_id, project_id, completion, metadata)
