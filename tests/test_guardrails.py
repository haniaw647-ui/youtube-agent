"""Real, unmocked — inserts real jobs against production Postgres for a
synthetic tenant and proves the guardrails actually block at the configured
limits, then cleans up.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from src.orchestrator.config import get_settings
from src.orchestrator.db import service_session
from src.orchestrator.guardrails import TenantLimitExceeded, check_tenant_job_limits
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()


@pytest.fixture(autouse=True)
def _low_limits(monkeypatch):
    # Real defaults (5/day, 2 concurrent) would mean inserting 5 real rows
    # just to prove the daily cap — lower them so the test is cheap and fast
    # without changing what's actually being proven.
    monkeypatch.setenv("MAX_JOBS_PER_TENANT_PER_DAY", "2")
    monkeypatch.setenv("MAX_CONCURRENT_JOBS_PER_TENANT", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Guardrail Test Tenant"))
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Guardrail Ch')"),
            {"id": CHANNEL_ID, "t": TENANT},
        )
        await session.commit()


CHANNEL_ID = uuid.uuid4()


async def _insert_job(job_id: str, overall_status: str) -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :c, 'topic_generation', :status)"
            ),
            {"id": job_id, "t": TENANT, "c": CHANNEL_ID, "status": overall_status},
        )
        await session.commit()


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(
                text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())


def test_passes_under_the_limit():
    asyncio.run(check_tenant_job_limits(TENANT))  # no jobs yet — must not raise


def test_daily_limit_blocks_after_the_configured_count():
    asyncio.run(_insert_job("job_grd_test_001", "done"))
    asyncio.run(_insert_job("job_grd_test_002", "done"))
    with pytest.raises(TenantLimitExceeded, match="Daily job limit"):
        asyncio.run(check_tenant_job_limits(TENANT))


def test_concurrency_limit_blocks_independent_of_daily_count():
    async def _run() -> None:
        async with service_session() as session:
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.commit()
        await _insert_job("job_grd_test_003", "running")
        with pytest.raises(TenantLimitExceeded, match="Too many jobs already in flight"):
            await check_tenant_job_limits(TENANT)

    asyncio.run(_run())
