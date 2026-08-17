import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.orchestrator.config import get_settings

settings = get_settings()
# NullPool: this engine is used both by FastAPI (one long-lived loop, where
# pooling would be fine) and by Celery workers (a fresh event loop per task
# via asyncio.run(), where a pooled connection from a prior loop is dead and
# reusing it raises "Event loop is closed"). NullPool opens a fresh connection
# per checkout so nothing outlives the loop that created it, at the cost of a
# new connection per session — fine at this phase's volume, revisit only if
# connection overhead is a measured bottleneck.
engine = create_async_engine(settings.database_url, poolclass=NullPool)
_session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def service_session() -> AsyncIterator[AsyncSession]:
    """Unscoped session connecting as the DATABASE_URL role (postgres), which
    bypasses RLS as a superuser. Only for internal/worker code that has already
    established tenant ownership at a higher level (e.g. a job row's tenant_id
    was validated when the job was created). Never use this to serve a
    tenant-facing API request directly from caller input."""
    async with _session_factory() as session:
        yield session


@asynccontextmanager
async def tenant_session(tenant_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """RLS-enforced session. Switches the connection to the `authenticated`
    Postgres role and sets request.jwt.claims for the duration of one
    transaction, so every table's `tenant_id = auth.uid()` policy is real
    database-level enforcement — not application logic a bug elsewhere could
    bypass. This is the only session tenant-facing API routes should use."""
    async with _session_factory() as session, session.begin():
        await session.execute(text("SELECT set_config('role', 'authenticated', true)"))
        claims = json.dumps({"sub": str(tenant_id), "role": "authenticated"})
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": claims},
        )
        yield session
