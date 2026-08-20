import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

from src.models.pipeline import PIPELINE_STAGES
from src.orchestrator.db import record_job_created, service_session
from src.orchestrator.guardrails import TenantLimitExceeded, check_tenant_job_limits
from src.orchestrator.timeutil import utcnow_naive

# IMPLEMENTATION_PLAN.md Phase 10: "Celery Beat triggers topic generation on
# each channel's cadence." Free-text posting_frequency (routes/channels.py)
# only ever meant one of these — unrecognized values are simply never due,
# rather than raising, so a typo doesn't crash the scheduler for everyone.
POSTING_FREQUENCIES: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "every_2_days": timedelta(days=2),
    "weekly": timedelta(days=7),
    "biweekly": timedelta(days=14),
    "monthly": timedelta(days=30),
}


def is_channel_due(
    posting_frequency: str | None, last_job_created_at: datetime | None, now: datetime
) -> bool:
    """Pure, no I/O. A channel with no jobs yet is always due (get it started);
    otherwise due once the configured interval has elapsed since the last job
    was *created* — not completed, since a slow/gated pipeline shouldn't cause
    a pile-up of jobs the moment it finally finishes."""
    interval = POSTING_FREQUENCIES.get(posting_frequency or "")
    if interval is None:
        return False
    if last_job_created_at is None:
        return True
    return now - last_job_created_at >= interval


async def run_scheduled_jobs() -> dict:
    now = utcnow_naive()

    async with service_session() as session:
        channels = (
            (
                await session.execute(
                    text(
                        "SELECT c.id, c.tenant_id, c.posting_frequency, "
                        "(SELECT max(j.created_at) FROM jobs j WHERE j.channel_id = c.id) "
                        "AS last_job_created_at "
                        "FROM channels c "
                        "WHERE c.posting_frequency IS NOT NULL"
                    )
                )
            )
            .mappings()
            .all()
        )

    started: list[str] = []
    skipped: list[str] = []

    for channel in channels:
        if not is_channel_due(
            channel["posting_frequency"], channel["last_job_created_at"], now
        ):
            continue

        try:
            await check_tenant_job_limits(channel["tenant_id"])
        except TenantLimitExceeded:
            # Not an error — the tenant is simply at capacity right now. The
            # channel stays overdue and gets picked up on a later beat tick.
            skipped.append(str(channel["id"]))
            continue

        job_id = await _create_scheduled_job(channel["tenant_id"], channel["id"])
        started.append(job_id)

    return {"started": started, "skipped_at_limit": skipped, "channels_checked": len(channels)}


async def _create_scheduled_job(tenant_id: uuid.UUID, channel_id: uuid.UUID) -> str:
    from src.workers.stage_runner import enqueue_stage

    first_stage = PIPELINE_STAGES[0]
    async with service_session() as session:
        seq = (await session.execute(text("SELECT nextval('job_id_seq')"))).scalar_one()
        job_id = f"job_{datetime.now().year}_{seq:05d}"
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :tenant_id, :channel_id, :stage, 'running')"
            ),
            {"id": job_id, "tenant_id": tenant_id, "channel_id": channel_id, "stage": first_stage},
        )
        await record_job_created(session, tenant_id, job_id)
        await session.commit()

    enqueue_stage(job_id, str(tenant_id), first_stage)
    return job_id
