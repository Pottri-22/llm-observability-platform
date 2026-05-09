"""Validate + persist trace events to ClickHouse.

For v0.1 we write synchronously per-request (single insert or batch). v0.2 will move
this behind an async batched writer (ring buffer flushed every N ms or N rows) once
ingest QPS justifies it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from app.db.clickhouse import get_client
from app.schemas.trace import TraceAccepted, TraceIngest
from app.services.cost import compute_cost_usd

log = structlog.get_logger()


_COLUMNS = [
    "trace_id",
    "org_id",
    "project_id",
    "ts",
    "model",
    "prompt",
    "completion",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "metadata",
]


def _row_for(trace: TraceIngest, *, org_id: str, project_id: str) -> tuple[Any, ...]:
    """Convert a validated TraceIngest into a ClickHouse row tuple."""
    trace_id = trace.trace_id or str(uuid.uuid4())
    ts = trace.ts or datetime.now(UTC)
    cost = (
        trace.cost_usd
        if trace.cost_usd is not None
        else compute_cost_usd(trace.model, trace.tokens_in, trace.tokens_out)
    )
    return (
        trace_id,
        org_id,
        project_id,
        ts,
        trace.model,
        trace.prompt,
        trace.completion,
        trace.tokens_in,
        trace.tokens_out,
        cost,
        trace.latency_ms,
        json.dumps(trace.metadata, default=str),
    )


async def write_traces(
    traces: list[TraceIngest],
    *,
    org_id: str,
    project_id: str,
) -> list[TraceAccepted]:
    """Persist N traces in a single batch insert.

    The clickhouse-connect client is synchronous; we run the insert in a thread to keep
    the event loop responsive.
    """
    if not traces:
        return []

    rows = [_row_for(t, org_id=org_id, project_id=project_id) for t in traces]
    client = get_client()

    def _do_insert() -> None:
        client.insert("traces", rows, column_names=_COLUMNS)

    await asyncio.to_thread(_do_insert)

    log.info(
        "trace.batch_written",
        org_id=org_id,
        project_id=project_id,
        count=len(rows),
    )

    return [TraceAccepted(trace_id=row[0]) for row in rows]
