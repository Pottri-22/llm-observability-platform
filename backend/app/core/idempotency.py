"""Redis-backed idempotency keys for trace ingest.

SDK retries can resend the same trace; idempotency keys make those resends safe.
We use Redis SET NX EX — atomic check-and-set with a TTL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as aioredis

from app.config import settings

# Default TTL: 24 hours. SDKs that retry beyond a day are accepting double-counts anyway.
_TTL_SECONDS = 24 * 60 * 60

_pool: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return a process-wide Redis client (connection pooled)."""
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _pool


async def claim_idempotency_key(
    key: str,
    *,
    ttl_seconds: int = _TTL_SECONDS,
) -> bool:
    """Try to claim an idempotency key.

    Returns True if the key was just claimed (fresh request).
    Returns False if the key was already claimed (duplicate).
    """
    if not key:
        # No key supplied → not a deduplicated request.
        return True
    redis = get_redis()
    full_key = f"aegis:idem:{key}"
    # SET key value NX EX ttl — atomic; returns None if key exists.
    result = await redis.set(full_key, "1", nx=True, ex=ttl_seconds)
    return bool(result)


@asynccontextmanager
async def redis_lifespan() -> AsyncIterator[None]:
    """For tests / scripts that want explicit teardown."""
    try:
        yield
    finally:
        global _pool
        if _pool is not None:
            await _pool.aclose()
            _pool = None
