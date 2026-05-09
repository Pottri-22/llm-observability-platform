"""Typed application settings, sourced from environment."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime config in one place. Env vars are case-insensitive."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Environment
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Postgres (async DSN — must use postgresql+asyncpg://)
    postgres_dsn: str = Field(
        default="postgresql+asyncpg://aegis:aegis_dev_pw@localhost:5432/aegis"
    )

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "aegis"
    clickhouse_password: str = "aegis_dev_pw"
    clickhouse_database: str = "aegis"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    aegis_jwt_secret: str = "replace_me_in_prod"

    # Trace writer
    trace_batch_size: int = 100
    trace_batch_flush_ms: int = 500


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this everywhere instead of Settings() directly."""
    return Settings()


settings = get_settings()
