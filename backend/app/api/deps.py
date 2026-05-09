"""FastAPI dependencies — auth + db session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.api_key import extract_prefix, verify_key
from app.core.exceptions import AuthError
from app.db.models import ApiKey, Project
from app.db.postgres import get_session


@dataclass(frozen=True)
class TenantContext:
    """The (org, project) pair an inbound request belongs to."""

    org_id: str
    project_id: str
    api_key_id: str


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_tenant(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """Resolve `Authorization: Bearer <api-key>` → TenantContext.

    Steps:
    1. Parse and prefix-lookup against `api_keys` (indexed)
    2. bcrypt-verify the full key against the stored hash
    3. Update last_used_at
    4. Return tenant context
    """
    if not authorization:
        raise AuthError("Missing Authorization header.")

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError("Authorization header must be `Bearer <api-key>`.")
    plaintext = parts[1].strip()
    if not plaintext.startswith("aegis_"):
        raise AuthError("Invalid API key format.")

    prefix = extract_prefix(plaintext)
    stmt = (
        select(ApiKey)
        .options(selectinload(ApiKey.project))
        .where(ApiKey.prefix == prefix)
        .limit(1)
    )
    result = await session.execute(stmt)
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise AuthError("Invalid API key.")
    if not verify_key(plaintext, api_key.key_hash):
        raise AuthError("Invalid API key.")

    # Touch last_used_at without blocking the request on a slow write.
    await session.execute(
        update(ApiKey).where(ApiKey.id == api_key.id).values(last_used_at=datetime.now(UTC))
    )
    await session.commit()

    project: Project = api_key.project
    return TenantContext(
        org_id=str(project.org_id),
        project_id=str(project.id),
        api_key_id=str(api_key.id),
    )


TenantDep = Annotated[TenantContext, Depends(get_tenant)]
