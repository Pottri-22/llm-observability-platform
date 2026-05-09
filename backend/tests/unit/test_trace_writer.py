"""Unit tests for trace_writer row-shaping logic."""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.trace import TraceIngest
from app.services.trace_writer import _row_for


def _ingest(**overrides: object) -> TraceIngest:
    base: dict[str, object] = {
        "model": "gpt-4o-mini",
        "prompt": "hi",
        "completion": "hello",
        "tokens_in": 5,
        "tokens_out": 5,
        "latency_ms": 250,
    }
    base.update(overrides)
    return TraceIngest(**base)  # type: ignore[arg-type]


def test_row_assigns_trace_id_if_missing() -> None:
    row = _row_for(_ingest(), org_id="org-1", project_id="proj-1")
    # trace_id is column index 0; should be a non-empty UUID-ish string
    assert isinstance(row[0], str)
    assert len(row[0]) >= 32


def test_row_preserves_client_trace_id() -> None:
    row = _row_for(_ingest(trace_id="custom-trace-abc"), org_id="o", project_id="p")
    assert row[0] == "custom-trace-abc"


def test_row_assigns_ts_if_missing() -> None:
    before = datetime.now(UTC)
    row = _row_for(_ingest(), org_id="o", project_id="p")
    after = datetime.now(UTC)
    ts = row[3]  # ts is column index 3
    assert isinstance(ts, datetime)
    assert before <= ts <= after


def test_row_uses_supplied_cost_when_present() -> None:
    row = _row_for(_ingest(cost_usd=0.0042), org_id="o", project_id="p")
    assert row[9] == 0.0042  # cost_usd is column index 9


def test_row_computes_cost_when_absent() -> None:
    row = _row_for(_ingest(model="gpt-4o-mini", tokens_in=1_000_000, tokens_out=1_000_000),
                   org_id="o", project_id="p")
    # Expected: $0.15 + $0.60 = $0.75
    assert row[9] > 0.74
    assert row[9] < 0.76


def test_row_serializes_metadata_as_json() -> None:
    row = _row_for(_ingest(metadata={"flow": "support", "user_tier": "premium"}),
                   org_id="o", project_id="p")
    metadata_json = row[11]  # metadata is column index 11
    assert isinstance(metadata_json, str)
    assert "flow" in metadata_json
    assert "support" in metadata_json
