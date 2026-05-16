"""Read traces back out of ClickHouse — backs the dashboard list + detail views.

Two invariants hold across every query in this module:

1. **Tenant scoping.** Every query carries a `project_id` predicate. It is not an
   optional filter — it is the isolation boundary. One tenant can never read another's
   traces, even by guessing a `trace_id`. The predicate is also the leading column of
   the table's `ORDER BY (project_id, ts, trace_id)`, so it's the most selective index
   hit available, not just a security gate.
2. **No string-built values.** We use clickhouse-connect server-side parameter binding
   (`{name:Type}` placeholders + a `parameters` dict). Only static, hard-coded SQL
   fragments are ever concatenated; filter *values* always travel in the params dict.
   This is the same "SQL injection is impossible" guarantee the ingest path makes.

The clickhouse-connect client is synchronous, so every query runs in a worker thread
via `asyncio.to_thread` — same pattern as `trace_writer.write_traces`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import structlog

from app.db.clickhouse import get_client
from app.schemas.common import PaginatedMeta
from app.schemas.trace import (
    EvaluationRecord,
    TraceDetail,
    TraceListItem,
    TraceListResponse,
)

log = structlog.get_logger()

_PROMPT_PREVIEW_CHARS = 120

# Column lists are module constants so the SELECT projection and the row-unpacking
# below can't silently drift apart.
_LIST_COLUMNS = (
    "trace_id, ts, model, tokens_in, tokens_out, cost_usd, latency_ms, "
    f"substring(prompt, 1, {_PROMPT_PREVIEW_CHARS}) AS prompt_preview"
)
_DETAIL_COLUMNS = (
    "trace_id, org_id, project_id, ts, model, prompt, completion, "
    "tokens_in, tokens_out, cost_usd, latency_ms, metadata, inserted_at"
)
_EVAL_COLUMNS = (
    "eval_id, evaluator, scores, reasoning, judge_model, "
    "latency_ms, cost_usd, status, error, created_at"
)


def _build_filters(
    *,
    project_id: str,
    model: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[str, dict[str, object]]:
    """Build a parameterized WHERE clause scoped to one project.

    Returns `(where_sql, params)`. `project_id` is always the first clause — it is the
    tenant isolation boundary, not an optional filter. Note what is and isn't dynamic:
    the placeholder *names* (`{model:String}`) are interpolated into SQL, but every
    actual value lives in `params` and is bound server-side by ClickHouse.
    """
    clauses = ["project_id = {project_id:String}"]
    params: dict[str, object] = {"project_id": project_id}

    if model is not None:
        clauses.append("model = {model:String}")
        params["model"] = model
    if since is not None:
        clauses.append("ts >= {since:DateTime64(3)}")
        params["since"] = since
    if until is not None:
        clauses.append("ts < {until:DateTime64(3)}")
        params["until"] = until

    return " AND ".join(clauses), params


async def list_traces(
    *,
    project_id: str,
    limit: int,
    offset: int,
    model: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> TraceListResponse:
    """Page through a project's traces, newest first.

    Runs two queries: one for the page of rows, one for the total row count so the
    dashboard can render "showing 1–50 of 1,284" and decide whether a next page exists.
    Both share the same WHERE clause and run together in one worker-thread hop.
    """
    where_sql, params = _build_filters(
        project_id=project_id, model=model, since=since, until=until
    )
    client = get_client()

    list_sql = (
        f"SELECT {_LIST_COLUMNS} FROM traces WHERE {where_sql} "
        "ORDER BY ts DESC LIMIT {limit:UInt32} OFFSET {offset:UInt32}"
    )
    count_sql = f"SELECT count() FROM traces WHERE {where_sql}"
    list_params = {**params, "limit": limit, "offset": offset}

    def _run() -> tuple[list[tuple[object, ...]], int]:
        rows = client.query(list_sql, parameters=list_params).result_rows
        total = client.query(count_sql, parameters=params).result_rows[0][0]
        return rows, total

    rows, total = await asyncio.to_thread(_run)

    items = [
        TraceListItem(
            trace_id=r[0],
            ts=r[1],
            model=r[2],
            tokens_in=r[3],
            tokens_out=r[4],
            cost_usd=r[5],
            latency_ms=r[6],
            prompt_preview=r[7],
        )
        for r in rows
    ]
    log.info(
        "trace.list_read",
        project_id=project_id,
        returned=len(items),
        total=total,
        offset=offset,
    )
    return TraceListResponse(
        traces=items,
        meta=PaginatedMeta(total=total, limit=limit, offset=offset),
    )


async def get_trace(*, project_id: str, trace_id: str) -> TraceDetail | None:
    """Fetch one fully-expanded trace, or `None` if it isn't in this project.

    The `project_id` predicate is load-bearing twice over: it keeps the lookup on the
    primary index, and it guarantees a tenant can't read another tenant's trace by
    guessing its id — a wrong-project `trace_id` simply returns zero rows.
    """
    client = get_client()
    sql = (
        f"SELECT {_DETAIL_COLUMNS} FROM traces "
        "WHERE project_id = {project_id:String} AND trace_id = {trace_id:String} "
        "LIMIT 1"
    )
    params = {"project_id": project_id, "trace_id": trace_id}

    def _run() -> list[tuple[object, ...]]:
        return client.query(sql, parameters=params).result_rows

    rows = await asyncio.to_thread(_run)
    if not rows:
        return None

    r = rows[0]
    return TraceDetail(
        trace_id=r[0],
        org_id=r[1],
        project_id=r[2],
        ts=r[3],
        model=r[4],
        prompt=r[5],
        completion=r[6],
        tokens_in=r[7],
        tokens_out=r[8],
        cost_usd=r[9],
        latency_ms=r[10],
        # metadata is stored as a JSON string by trace_writer; parse it back to a dict.
        metadata=json.loads(r[11]) if r[11] else {},
        inserted_at=r[12],
    )


async def list_evaluations_for_trace(
    *, project_id: str, trace_id: str
) -> list[EvaluationRecord]:
    """All evaluator rows for one trace, newest first. Project-scoped.

    A trace can have multiple eval rows — re-runs, and (later in v0.2) one row
    per evaluator (Judge, RAGAS, BERTScore, PII). The dashboard renders them
    grouped by `evaluator`, so we hand back the whole set in one go rather than
    making it filter."""
    client = get_client()
    sql = (
        f"SELECT {_EVAL_COLUMNS} FROM evaluations "
        "WHERE project_id = {project_id:String} AND trace_id = {trace_id:String} "
        "ORDER BY created_at DESC"
    )
    params = {"project_id": project_id, "trace_id": trace_id}

    def _run() -> list[tuple[object, ...]]:
        return client.query(sql, parameters=params).result_rows

    rows = await asyncio.to_thread(_run)
    return [
        EvaluationRecord(
            eval_id=r[0],
            evaluator=r[1],
            # ClickHouse Map(String, Float64) is returned by clickhouse-connect
            # as a Python dict; coerce defensively so a future driver change
            # that hands back tuples doesn't break us.
            scores=dict(r[2]),
            reasoning=r[3],
            judge_model=r[4],
            latency_ms=r[5],
            cost_usd=r[6],
            status=r[7],
            error=r[8],
            created_at=r[9],
        )
        for r in rows
    ]
