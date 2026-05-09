# Multi-stage build: builder installs deps, final is a slim runtime.
# Final image runs as non-root on a minimal Python base.

FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy

# Install uv
RUN pip install --no-cache-dir uv==0.4.27

WORKDIR /app

# Copy lock + pyproject for dependency caching
COPY pyproject.toml uv.lock* ./

# Install runtime deps only (no dev) into a venv we'll copy out
RUN uv sync --frozen --no-dev --no-install-project 2>/dev/null || uv sync --no-dev --no-install-project

# Copy source last to maximize cache reuse
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Non-root user for runtime
RUN groupadd --system --gid 1001 aegis \
    && useradd --system --uid 1001 --gid aegis --no-create-home aegis

WORKDIR /app

COPY --from=builder --chown=aegis:aegis /app /app

USER aegis

EXPOSE 8000

# Healthcheck hits liveness endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/healthz', timeout=3); exit(0 if r.status_code == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
