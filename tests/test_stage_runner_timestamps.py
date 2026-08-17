"""Regression test for a real bug: every job_stages/jobs timestamp write in
stage_runner.py used datetime.now(UTC) (timezone-aware) against columns that
are `timestamp without time zone` — asyncpg rejects that combination outright
("can't subtract offset-naive and offset-aware datetimes"). This had never
been caught because the isolation test stubs out enqueue_stage, so
execute_stage's actual DB writes were never exercised end to end. Fixed via
timeutil.utcnow_naive(); this test proves the fix against the real DB rather
than trusting it by inspection."""

import uuid

import pytest
from sqlalchemy import text

from src.orchestrator.db import service_session
from src.workers.stage_runner import _insert_running_stage

TENANT_ID = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()
JOB_ID = f"job_test_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_insert_running_stage_writes_a_valid_timestamp():
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO tenants (id, display_name) VALUES (:id, 'Timestamp Test Tenant')"),
            {"id": TENANT_ID},
        )
        await session.execute(
            text(
                "INSERT INTO channels (id, tenant_id, name) "
                "VALUES (:id, :tenant_id, 'Timestamp Test Channel')"
            ),
            {"id": CHANNEL_ID, "tenant_id": TENANT_ID},
        )
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :tenant_id, :channel_id, 'topic_generation', 'running')"
            ),
            {"id": JOB_ID, "tenant_id": TENANT_ID, "channel_id": CHANNEL_ID},
        )
        await session.commit()

    try:
        stage_row_id = await _insert_running_stage(JOB_ID, TENANT_ID, "topic_generation")

        async with service_session() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT status, started_at FROM job_stages WHERE id = :id"),
                        {"id": stage_row_id},
                    )
                )
                .mappings()
                .one()
            )

        assert row["status"] == "running"
        assert row["started_at"] is not None
    finally:
        async with service_session() as session:
            await session.execute(
                text("DELETE FROM job_stages WHERE job_id = :job_id"), {"job_id": JOB_ID}
            )
            await session.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": JOB_ID})
            await session.execute(text("DELETE FROM channels WHERE id = :id"), {"id": CHANNEL_ID})
            await session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": TENANT_ID})
            await session.commit()
