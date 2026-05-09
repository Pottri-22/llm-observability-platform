"""ClickHouse client wrapper.

Uses clickhouse-connect (HTTP). Sync client; we run inserts in threadpool when called
from async code. For v0.1 throughput targets (10s of req/sec), this is plenty.
"""

from __future__ import annotations

from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from app.config import settings

_client: Client | None = None


def init_clickhouse() -> Client:
    """Build the singleton client at startup."""
    global _client
    if _client is not None:
        return _client
    _client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        compress=True,
        connect_timeout=5,
    )
    return _client


def close_clickhouse() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def get_client() -> Client:
    if _client is None:
        raise RuntimeError("ClickHouse not initialized. Call init_clickhouse() first.")
    return _client


def healthcheck() -> dict[str, Any]:
    """Used by /readyz."""
    if _client is None:
        return {"ok": False, "reason": "not_initialized"}
    try:
        result = _client.query("SELECT 1").result_rows
        return {"ok": result == [(1,)]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": str(e)}


# Trace table DDL — kept here so scripts/clickhouse_migrate.py can run it idempotently.
TRACES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS traces
(
    trace_id String,
    org_id String,
    project_id String,
    ts DateTime64(3),
    model String,
    prompt String,
    completion String,
    tokens_in UInt32,
    tokens_out UInt32,
    cost_usd Float64,
    latency_ms UInt32,
    metadata String,
    inserted_at DateTime DEFAULT now()
)
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(ts)
ORDER BY (project_id, ts, trace_id)
SETTINGS index_granularity = 8192
""".strip()
