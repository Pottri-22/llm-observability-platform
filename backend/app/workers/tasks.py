"""Celery task definitions.

For EVAL-A this is just one task — `evaluate_trace` — and its body is a
placeholder that writes a dummy `evaluator="noop"` row to ClickHouse. The
shape exists so the ingest path can enqueue jobs and we can verify the wire
end-to-end (POST → Redis queue → worker → eval row). EVAL-B replaces the body
with the real LLM-as-Judge call; the public signature stays the same.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import clickhouse_connect
import structlog
from clickhouse_connect.driver.client import Client

from app.config import settings
from app.workers.celery_app import celery_app

log = structlog.get_logger()

# Worker processes are separate from the FastAPI app, so they need their own
# ClickHouse client. Lazy-initialized on first task run — Celery's pool model
# means one client per worker process, reused across all tasks that process
# runs.
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


@celery_app.task(name="evaluate_trace", bind=True, max_retries=3, default_retry_delay=10)
def evaluate_trace(self, trace_id: str, org_id: str, project_id: str) -> None:  # type: ignore[no-untyped-def]
    """Run all evaluators against one trace and write an `evaluations` row.

    EVAL-A: writes a single placeholder row with `evaluator="noop"` so the pipe
    is end-to-end provable. EVAL-B will replace this body with the LLM-as-Judge
    rubric call. The Celery contract stays identical — same task name, same
    args — so the ingest enqueue site never needs to change again.

    `bind=True` exposes `self`, which gives us `self.retry(...)` once the real
    evaluator can fail transiently (Groq 503, judge timeout). Three retries with
    a 10-second base delay is a sane v0.2 starting point; tune from real data.
    """
    row = (
        str(uuid.uuid4()),
        trace_id,
        org_id,
        project_id,
        "noop",
        {"placeholder": 0.0},
        "",
        "",
        0,
        0.0,
        "ok",
        "",
        datetime.now(UTC),
    )
    _ch().insert("evaluations", [row], column_names=_EVAL_COLUMNS)
    log.info(
        "eval.written",
        trace_id=trace_id,
        project_id=project_id,
        evaluator="noop",
    )
