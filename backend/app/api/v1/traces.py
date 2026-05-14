"""Trace ingest endpoints — the only endpoints SDKs talk to."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import TenantDep
from app.core.exceptions import NotFoundError
from app.core.idempotency import claim_idempotency_key
from app.schemas.trace import (
    BatchAccepted,
    TraceAccepted,
    TraceBatch,
    TraceDetail,
    TraceIngest,
    TraceListResponse,
)
from app.services.trace_reader import get_trace, list_traces
from app.services.trace_writer import write_traces

router = APIRouter()


@router.post(
    "/traces",
    response_model=TraceAccepted,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a single trace",
)
async def ingest_trace(payload: TraceIngest, tenant: TenantDep) -> TraceAccepted:
    """Persist one trace event for the authenticated tenant.

    If `idempotency_key` is supplied and we've seen it in the last 24 h, the trace is
    silently dropped and `duplicate=true` is returned (the SDK should treat this as success).
    """
    if payload.idempotency_key:
        claimed = await claim_idempotency_key(payload.idempotency_key)
        if not claimed:
            return TraceAccepted(trace_id=payload.trace_id or "", duplicate=True)

    accepted = await write_traces(
        [payload], org_id=tenant.org_id, project_id=tenant.project_id
    )
    return accepted[0]


@router.post(
    "/traces/batch",
    response_model=BatchAccepted,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a batch of traces",
)
async def ingest_batch(payload: TraceBatch, tenant: TenantDep) -> BatchAccepted:
    """Persist up to 500 traces in one request. SDK's primary code path."""
    # Note: idempotency is a per-trace concern; for v0.1 we apply it here naively.
    fresh: list[TraceIngest] = []
    skipped: list[TraceAccepted] = []

    for trace in payload.traces:
        if trace.idempotency_key:
            claimed = await claim_idempotency_key(trace.idempotency_key)
            if not claimed:
                skipped.append(TraceAccepted(trace_id=trace.trace_id or "", duplicate=True))
                continue
        fresh.append(trace)

    accepted = await write_traces(
        fresh, org_id=tenant.org_id, project_id=tenant.project_id
    )
    return BatchAccepted(accepted=[*accepted, *skipped])


@router.get(
    "/traces",
    response_model=TraceListResponse,
    summary="List traces for the tenant's project",
)
async def read_traces(
    tenant: TenantDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    model: Annotated[str | None, Query(max_length=200)] = None,
    since: Annotated[
        datetime | None, Query(description="Only traces at or after this time (ISO 8601).")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Only traces strictly before this time (ISO 8601).")
    ] = None,
) -> TraceListResponse:
    """Page through the calling project's traces, newest first.

    Scoped to `tenant.project_id` — there is no parameter to ask for another tenant's
    traces. The project predicate is injected server-side from the bcrypt-verified API
    key, never from the request. `limit` is capped at 200 so one call can't scan an
    unbounded page.
    """
    return await list_traces(
        project_id=tenant.project_id,
        limit=limit,
        offset=offset,
        model=model,
        since=since,
        until=until,
    )


@router.get(
    "/traces/{trace_id}",
    response_model=TraceDetail,
    summary="Fetch one trace by id",
)
async def read_trace(trace_id: str, tenant: TenantDep) -> TraceDetail:
    """Fetch a single fully-expanded trace owned by the calling project.

    A `trace_id` that exists but belongs to another project is indistinguishable from
    one that doesn't exist at all — both return 404. That's deliberate: it leaks no
    information about other tenants' data.
    """
    detail = await get_trace(project_id=tenant.project_id, trace_id=trace_id)
    if detail is None:
        raise NotFoundError(f"No trace {trace_id!r} in this project.")
    return detail
