"""Async SQLAlchemy 2.x engine + session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


async def init_postgres() -> None:
    """Build the engine + session factory at startup."""
    global _engine, _sessionmaker
    if _engine is not None:
        return
    _engine = create_async_engine(
        settings.postgres_dsn,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )
    _sessionmaker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def close_postgres() -> None:
    """Dispose pool at shutdown."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Postgres engine not initialized. Call init_postgres() first.")
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for an async session."""
    if _sessionmaker is None:
        raise RuntimeError("Postgres not initialized.")
    async with _sessionmaker() as session:
        yield session


async def healthcheck() -> dict[str, Any]:
    """Probe the engine. Used by /readyz."""
    if _engine is None:
        return {"ok": False, "reason": "not_initialized"}
    try:
        async with _engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": str(e)}
