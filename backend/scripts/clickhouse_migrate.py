"""Run ClickHouse DDL idempotently.

Usage:
    uv run python -m scripts.clickhouse_migrate
"""

from __future__ import annotations

import sys

import clickhouse_connect

from app.config import settings
from app.db.clickhouse import EVALUATIONS_TABLE_DDL, TRACES_TABLE_DDL


def main() -> int:
    print(f"Connecting to ClickHouse at {settings.clickhouse_host}:{settings.clickhouse_port}…")
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )

    db = settings.clickhouse_database
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=db,
    )

    print("Applying traces table DDL…")
    client.command(TRACES_TABLE_DDL)
    print("Applying evaluations table DDL…")
    client.command(EVALUATIONS_TABLE_DDL)
    print("OK — traces + evaluations tables ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
