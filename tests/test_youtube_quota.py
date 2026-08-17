import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.youtube_quota import (
    DEFAULT_DAILY_QUOTA,
    UPLOAD_COST_UNITS,
    get_todays_quota_usage,
    record_quota_usage,
    would_exceed_daily_quota,
)

TENANT_ID = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()
JOB_ID = f"job_test_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_would_exceed_daily_quota_boundary():
    with patch(
        "src.orchestrator.youtube_quota.get_todays_quota_usage",
        new=AsyncMock(return_value=DEFAULT_DAILY_QUOTA - 100),
    ):
        assert await would_exceed_daily_quota(200) is True
        assert await would_exceed_daily_quota(100) is False
        assert await would_exceed_daily_quota(50) is False


@pytest.mark.asyncio
async def test_record_and_sum_quota_usage_against_real_db():
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, display_name) VALUES (:id, 'Quota Test Tenant')"),
            {"id": TENANT_ID},
        )
        await session.execute(
            text(
                "INSERT INTO channels (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, 'Quota Test Channel')"
            ),
            {"id": CHANNEL_ID, "tenant_id": TENANT_ID},
        )
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :tenant_id, :channel_id, 'youtube_upload', 'running')"
            ),
            {"id": JOB_ID, "tenant_id": TENANT_ID, "channel_id": CHANNEL_ID},
        )
        await session.commit()

    try:
        before = await get_todays_quota_usage()
        await record_quota_usage(str(TENANT_ID), JOB_ID, "videos.insert", UPLOAD_COST_UNITS)
        after = await get_todays_quota_usage()

        assert after == before + UPLOAD_COST_UNITS
    finally:
        async with service_session() as session:
            await session.execute(
                text("DELETE FROM api_call_logs WHERE job_id = :job_id"), {"job_id": JOB_ID}
            )
            await session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": JOB_ID})
            await session.execute(text("DELETE FROM channels WHERE id = :id"), {"id": CHANNEL_ID})
            await session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": TENANT_ID})
            await session.commit()
