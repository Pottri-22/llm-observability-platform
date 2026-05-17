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

    # LLM-as-Judge (v0.2 eval engine). The judge call goes through LiteLLM, so
    # `judge_model` is a LiteLLM provider-prefixed string ("groq/...",
    # "openai/...", "anthropic/...", "ollama/..."). Switching providers is a
    # config change — no code edit. Default stays on Groq's free tier so the
    # eval engine costs $0 during the sprint.
    judge_model: str = "groq/llama-3.3-70b-versatile"
    judge_timeout_s: float = 15.0  # per attempt; one judge call should be ~1-3s
    judge_runs: int = 3  # G-Eval median-of-N stabilization; README §6.4 spec

    # RAGAS evaluator — DIY rubrics via the same LiteLLM gateway as the judge,
    # one rubric call per metric per run. With three metrics on a full RAG
    # trace this is 9 calls; free on Groq so it's fine, dial down via this
    # knob if you switch to a paid provider. README §6.4 spec.
    ragas_runs: int = 3

    # Provider API keys. LiteLLM reads these from the env at call time; we mirror
    # them here so app.config is the single source of truth and tests can inject.
    # Worker containers receive whichever are set in the host .env (see compose).
    groq_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Use this everywhere instead of Settings() directly."""
    return Settings()


settings = get_settings()
