"""Custom exception hierarchy + FastAPI error handlers."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request, status
from fastapi.responses import ORJSONResponse

log = structlog.get_logger()


class AegisError(Exception):
    """Base for all Aegis domain errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class AuthError(AegisError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "auth_error"


class NotFoundError(AegisError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationError(AegisError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "validation_error"


class RateLimitError(AegisError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


def register_exception_handlers(app: FastAPI) -> None:
    """Map AegisError → JSON response. Unexpected errors get a generic 500."""

    @app.exception_handler(AegisError)
    async def _aegis_handler(request: Request, exc: AegisError) -> ORJSONResponse:  # noqa: RUF029
        log.warning("aegis.error", code=exc.code, message=exc.message, path=request.url.path)
        return ORJSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> ORJSONResponse:  # noqa: RUF029
        log.exception("aegis.unhandled", path=request.url.path, error=str(exc))
        return ORJSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
        )
