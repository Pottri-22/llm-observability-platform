"""FastAPI application factory + lifespan + middleware wiring."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.v1 import health, traces
from app.config import settings
from app.core.exceptions import AegisError, register_exception_handlers
from app.core.logging import configure_logging
from app.db.clickhouse import close_clickhouse, init_clickhouse
from app.db.postgres import close_postgres, init_postgres

log = structlog.get_logger()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request_id to every request and bind it into structlog context."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level, settings.env)
    log.info("aegis.startup", version=__version__, env=settings.env)
    await init_postgres()
    init_clickhouse()
    log.info("aegis.ready")
    try:
        yield
    finally:
        log.info("aegis.shutdown")
        await close_postgres()
        close_clickhouse()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Aegis",
        version=__version__,
        description="Open-source LLM observability + evaluation platform.",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router, tags=["health"])
    app.include_router(traces.router, prefix="/v1", tags=["traces"])

    return app


app = create_app()


# Surface AegisError to silence unused-import warnings in modules that import it.
__all__ = ["app", "create_app", "AegisError"]
