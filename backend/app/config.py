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

    # LLM-as-Judge (v0.2 eval engine). Defaults point at Groq's free tier so
    # the eval engine stays $0 during the sprint. v0.3 swaps to LiteLLM and
    # supports paid providers without touching this code.
    judge_model: str = "llama-3.3-70b-versatile"
    judge_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""  # populated from the worker container's env
    judge_timeout_s: float = 15.0  # per attempt; one judge call should be ~1-3s
    judge_runs: int = 3  # G-Eval median-of-N stabilization; README §6.4 spec


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this everywhere instead of Settings() directly."""
    return Settings()


settings = get_settings()
