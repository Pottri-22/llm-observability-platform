"""Pydantic schemas for trace ingest."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TraceIngest(BaseModel):
    """Single trace event uploaded by an SDK."""

    trace_id: str | None = Field(
        default=None,
        description="Optional client-supplied trace id; server assigns UUID4 if absent.",
        max_length=128,
    )
    ts: datetime | None = Field(
        default=None,
        description="When the LLM call occurred. Server uses now() if absent.",
    )
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(default="", max_length=200_000)
    completion: str = Field(default="", max_length=200_000)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="Optional client-computed cost. If absent, server computes from tokens + model.",
    )
    latency_ms: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="If set, retries with the same key are deduplicated for 24h.",
    )

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        # Crude cap so a misbehaving SDK can't DOS the ingest path.
        if len(str(v)) > 10_000:
            raise ValueError("metadata is too large (>10KB serialized)")
        return v


class TraceBatch(BaseModel):
    """Batch ingest payload."""

    traces: list[TraceIngest] = Field(min_length=1, max_length=500)


class TraceAccepted(BaseModel):
    trace_id: str
    duplicate: bool = False


class BatchAccepted(BaseModel):
    accepted: list[TraceAccepted]
