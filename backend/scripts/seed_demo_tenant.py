"""Seed one demo Org + Project + ApiKey, print the plaintext key to stdout.

Idempotent on the org name "demo" — if it already exists, mints a fresh key under it
and prints it. The plaintext is never persisted; copy it now or rerun.

Usage:
    uv run python -m scripts.seed_demo_tenant
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.api_key import generate_api_key
from app.config import settings
from app.db.models import ApiKey, Org, Project


async def _seed() -> str:
    engine = create_async_engine(settings.postgres_dsn, echo=False)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async with sessionmaker() as session:
        # Find or create demo org
        org = (await session.execute(select(Org).where(Org.name == "demo"))).scalar_one_or_none()
        if org is None:
            org = Org(name="demo")
            session.add(org)
            await session.flush()

        # Find or create demo project under it
        project = (
            await session.execute(
                select(Project).where(Project.org_id == org.id, Project.name == "demo-project")
            )
        ).scalar_one_or_none()
        if project is None:
            project = Project(org_id=org.id, name="demo-project")
            session.add(project)
            await session.flush()

        # Always mint a fresh API key (we cannot recover an old plaintext).
        gen = generate_api_key(env="live" if settings.env == "prod" else "dev")
        api_key = ApiKey(
            project_id=project.id,
            name="seed",
            prefix=gen.prefix,
            key_hash=gen.key_hash,
        )
        session.add(api_key)
        await session.commit()

    await engine.dispose()
    return gen.plaintext


def main() -> int:
    plaintext = asyncio.run(_seed())
    print()
    print("Demo tenant seeded.")
    print("  Org      : demo")
    print("  Project  : demo-project")
    print("  API key  :", plaintext)
    print()
    print("Try it:")
    print(
        '  curl -X POST http://localhost:8000/v1/traces \\\n'
        f'    -H "Authorization: Bearer {plaintext}" \\\n'
        '    -H "Content-Type: application/json" \\\n'
        '    -d \'{"model":"gpt-4o-mini","prompt":"hi","completion":"hello",'
        '"tokens_in":2,"tokens_out":2,"latency_ms":250}\''
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
