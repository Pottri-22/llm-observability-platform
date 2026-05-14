"""The trace event — the unit the SDK buffers and ships to Aegis.

A `TraceEvent` is the SDK-side shape. `to_payload()` maps it onto the backend's
`TraceIngest` wire schema (`POST /v1/traces/batch`). The two are deliberately separate
types: the SDK owns its own representation so a backend schema tweak doesn't ripple
through instrumentation code, and vice versa.

Mapping notes (why the shapes differ):
  * The backend schema has flat `prompt` / `completion` strings, not a messages list.
    We JSON-serialize the full `messages` list into `prompt` so the trace-detail view
    can replay the whole conversation, not just the last turn.
  * The backend has no `status` / `streamed` field, so those — plus `provider` and any
    error repr — ride along inside `metadata`.
  * `cost_usd` is intentionally never sent. The server recomputes it from
    `(model, tokens_in, tokens_out)`; a buggy SDK must not be able to lie about cost.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    """UTC timestamp, ISO-8601 with offset — what the backend's `ts` field parses."""
    return datetime.now(UTC).isoformat()


@dataclass
class TraceEvent:
    """One instrumented LLM call, captured by the SDK and queued for upload.

    `trace_id` and `idempotency_key` are both minted client-side at construction:
      * `trace_id` identifies the trace forever (the dashboard URL, share links).
      * `idempotency_key` lets the backend dedupe SDK retries — if a batch POST is
        retried after a timeout, the same key arrives twice and the second write is
        dropped. They start equal but are distinct fields so a future SDK could retry
        with a fresh trace_id if it ever needed to.
    """

    model: str
    prompt: str
    completion: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    status: str = "ok"  # "ok" | "error" — rides in metadata on the wire
    streamed: bool = False
    provider: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        # idempotency_key defaults to trace_id: each event is traced exactly once, so
        # one identifier serves both roles. Kept as its own field for future divergence.
        if not self.idempotency_key:
            self.idempotency_key = self.trace_id

    @classmethod
    def from_call(
        cls,
        *,
        model: str,
        messages: list[dict[str, Any]],
        completion: str,
        tokens_in: int | None,
        tokens_out: int | None,
        latency_ms: float,
        status: str,
        streamed: bool,
        provider: str | None = None,
        error: str | None = None,
        user_metadata: dict[str, Any] | None = None,
    ) -> TraceEvent:
        """Build a TraceEvent from the raw pieces an instrumentation wrapper captures.

        `messages` is JSON-serialized into `prompt`. `tokens_*` may be None (streaming
        calls without `include_usage`); we coerce to 0 so the backend's `ge=0` validator
        is satisfied and the cost calc just yields 0 rather than rejecting the trace.
        """
        return cls(
            model=model,
            prompt=json.dumps(messages, default=str),
            completion=completion,
            tokens_in=tokens_in or 0,
            tokens_out=tokens_out or 0,
            latency_ms=int(latency_ms),
            status=status,
            streamed=streamed,
            provider=provider,
            error=error,
            metadata=dict(user_metadata or {}),
        )

    def to_payload(self) -> dict[str, Any]:
        """Render to the backend `TraceIngest` wire shape.

        `status` / `streamed` / `provider` / `error` are folded into `metadata` because
        the backend schema has no top-level home for them. User-supplied metadata keys
        are preserved alongside; the SDK-owned keys are namespaced under `aegis` so they
        can't collide with a user's `{"status": ...}`.
        """
        meta: dict[str, Any] = dict(self.metadata)
        meta["aegis"] = {
            "status": self.status,
            "streamed": self.streamed,
            "provider": self.provider,
        }
        if self.error is not None:
            meta["aegis"]["error"] = self.error
        return {
            "trace_id": self.trace_id,
            "ts": self.ts,
            "model": self.model,
            "prompt": self.prompt,
            "completion": self.completion,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "metadata": meta,
            "idempotency_key": self.idempotency_key,
        }
