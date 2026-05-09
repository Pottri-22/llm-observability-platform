.PHONY: help install dev test lint typecheck format migrate seed clickhouse-init clean

help:
	@echo "Aegis backend — common commands"
	@echo ""
	@echo "  make install         Install deps via uv"
	@echo "  make dev             Run FastAPI with reload on :8000"
	@echo "  make test            Run pytest suite"
	@echo "  make lint            Ruff lint"
	@echo "  make typecheck       Mypy strict typecheck"
	@echo "  make format          Ruff format"
	@echo "  make migrate         Apply Alembic Postgres migrations"
	@echo "  make clickhouse-init Run ClickHouse DDL"
	@echo "  make seed            Seed one demo tenant + print API key"
	@echo "  make clean           Remove caches"

install:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	uv run pytest

lint:
	uv run ruff check app tests scripts

typecheck:
	uv run mypy app

format:
	uv run ruff format app tests scripts

migrate:
	uv run alembic upgrade head

clickhouse-init:
	uv run python -m scripts.clickhouse_migrate

seed:
	uv run python -m scripts.seed_demo_tenant

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
