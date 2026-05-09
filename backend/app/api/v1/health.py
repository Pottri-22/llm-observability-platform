"""Health endpoints.

  /healthz — liveness, no deps. Always 200 if the process is alive.
  /readyz  — readiness, checks postgres + clickhouse + redis.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.db import clickhouse as ch
from app.db import postgres as pg

router = APIRouter()


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe")
async def readyz(response: Response) -> dict[str, object]:
    pg_status = await pg.healthcheck()
    ch_status = ch.healthcheck()
    ok = bool(pg_status.get("ok")) and bool(ch_status.get("ok"))
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "ok": ok,
        "postgres": pg_status,
        "clickhouse": ch_status,
    }
