"""Shared response shapes."""

from __future__ import annotations

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class PaginatedMeta(BaseModel):
    total: int
    limit: int
    offset: int
