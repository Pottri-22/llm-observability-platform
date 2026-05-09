"""Shared test fixtures.

Unit tests run without infrastructure. Integration tests use testcontainers to spin
up real Postgres + ClickHouse + Redis. Mark integration tests with @pytest.mark.integration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# Integration fixtures intentionally lazy — only imported when an integration test asks.
# This keeps unit-test runs fast (no docker required).


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[object]:
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", username="aegis", password="aegis_test", dbname="aegis") as pg:
        yield pg


@pytest.fixture(scope="session")
def clickhouse_container() -> Iterator[object]:
    pytest.importorskip("testcontainers.core")
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer("clickhouse/clickhouse-server:24.8-alpine")
        .with_env("CLICKHOUSE_DB", "aegis")
        .with_env("CLICKHOUSE_USER", "aegis")
        .with_env("CLICKHOUSE_PASSWORD", "aegis_test")
        .with_exposed_ports(8123)
    )
    container.start()
    try:
        wait_for_logs(container, "Ready for connections", timeout=60)
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def redis_container() -> Iterator[object]:
    pytest.importorskip("testcontainers.core")
    from testcontainers.core.container import DockerContainer

    container = DockerContainer("redis:7-alpine").with_exposed_ports(6379)
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def api_client() -> AsyncIterator[object]:
    """Async HTTP client for FastAPI app. Wired in integration tests."""
    pytest.importorskip("httpx")
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
