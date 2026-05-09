"""Trace ingest endpoints — the only endpoints SDKs talk to."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import TenantDep
from app.core.idempotency import claim_idempotency_key
from app.schemas.trace import (
    BatchAccepted,
    TraceAccepted,
    TraceBatch,
    TraceIngest,
)
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
