"""Integration test — full POST /v1/traces flow against real Postgres + ClickHouse.

Marked as integration; require Docker. Skip cleanly if Docker / testcontainers unavailable.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skip(
    reason="Wires up testcontainers + Alembic + ClickHouse migrate against the live app. "
    "Enable in W2 once the test harness is exercised end-to-end.",
)
async def test_ingest_writes_to_clickhouse(
    postgres_container: object,
    clickhouse_container: object,
    redis_container: object,
) -> None:
    """Happy-path: seed a tenant → POST /v1/traces → assert row in ClickHouse."""
    # Intentionally a placeholder until W2. The fixtures + harness are in conftest.py.
    # When enabling, set env vars from container.get_connection_url() / get_exposed_port(),
    # run `alembic upgrade head`, run `scripts.clickhouse_migrate.main()`, then exercise:
    #   1. POST /v1/traces with the seeded API key
    #   2. assert response 201 + trace_id present
    #   3. SELECT count() FROM traces == 1
    assert os.environ.get("AEGIS_INTEGRATION") != "block"  # placeholder assertion
