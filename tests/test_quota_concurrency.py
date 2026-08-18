"""Real, unmocked — IMPLEMENTATION_PLAN.md Phase 10: 'confirm shared YouTube
quota holds ... under concurrent load, not just single-request tests.' Runs
real concurrent reservations against production Postgres (not mocked) and
proves the platform-wide quota ceiling is never breached, even when several
uploads race to reserve at nearly the same instant.

This test originally caught a real TOCTOU race: would_exceed_daily_quota()
and record_quota_usage() as two separate round trips let 3 concurrent
1,600-unit attempts all pass the check before any of them recorded usage —
4,800 units landed against a 3,000-unit headroom. Fixed with an atomic,
advisory-lock-guarded reserve_quota_or_raise() (see youtube_quota.py); this
test now exercises that fixed path directly.
"""

import asyncio
import uuid

from sqlalchemy import text

from src.orchestrator import youtube_quota
from src.orchestrator.db import service_session
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()
JOB_IDS = ["job_quota_race_test_0", "job_quota_race_test_1", "job_quota_race_test_2"]


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Quota Concurrency Test Tenant"))

    async def _seed() -> None:
        async with service_session() as session:
            await session.execute(
                text("INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Quota Ch')"),
                {"id": CHANNEL_ID, "t": TENANT},
            )
            for job_id in JOB_IDS:
                await session.execute(
                    text(
                        "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, "
                        "overall_status) VALUES (:id, :t, :c, 'youtube_upload', 'running')"
                    ),
                    {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
                )
            await session.commit()

    asyncio.run(_seed())


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text("DELETE FROM api_call_logs WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())


async def _attempt_reservation(job_id: str, cost: int) -> bool:
    try:
        await youtube_quota.reserve_quota_or_raise(str(TENANT), job_id, "videos.insert", cost)
        return True
    except youtube_quota.QuotaExceededError:
        return False


def test_concurrent_reservations_never_exceed_the_daily_ceiling(monkeypatch) -> None:
    async def _run() -> None:
        used_before = await youtube_quota.get_todays_quota_usage()
        # Headroom for exactly one 1,600-unit upload — three concurrent
        # attempts is enough to expose a check-then-record race if one exists.
        headroom = 3000
        monkeypatch.setattr(youtube_quota, "DEFAULT_DAILY_QUOTA", used_before + headroom)

        results = await asyncio.gather(
            *[_attempt_reservation(job_id, 1600) for job_id in JOB_IDS]
        )

        used_after = await youtube_quota.get_todays_quota_usage()
        actually_recorded = used_after - used_before

        assert actually_recorded <= headroom, (
            f"quota ceiling breached under concurrency: {actually_recorded} units "
            f"recorded against a {headroom}-unit headroom "
            f"({sum(results)}/{len(JOB_IDS)} concurrent reservations were accepted)"
        )

    asyncio.run(_run())
