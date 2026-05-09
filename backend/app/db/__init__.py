"""Database layer — Postgres (metadata) + ClickHouse (traces)."""

from app.db.models import Base

__all__ = ["Base"]
