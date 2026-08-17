import uuid

from sqlalchemy import text

from src.orchestrator.config import get_settings
from src.orchestrator.db import tenant_session
from src.orchestrator.timeutil import utcnow_naive


class TenantLimitExceeded(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


async def check_tenant_job_limits(tenant_id: uuid.UUID) -> None:
    """Runs inside the caller's own RLS-scoped session — a tenant can only ever
    count its own jobs, same as every other tenant-scoped read. Raises rather
    than returning a bool so callers can't accidentally ignore it."""
    settings = get_settings()
    today_start = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)

    async with tenant_session(tenant_id) as session:
        created_today = (
            await session.execute(
                text("SELECT count(*) FROM jobs WHERE created_at >= :since"),
                {"since": today_start},
            )
        ).scalar_one()
        if created_today >= settings.max_jobs_per_tenant_per_day:
            raise TenantLimitExceeded(
                f"Daily job limit reached ({settings.max_jobs_per_tenant_per_day}/day). "
                "Try again after UTC midnight."
            )

        running_now = (
            await session.execute(
                text("SELECT count(*) FROM jobs WHERE overall_status = 'running'")
            )
        ).scalar_one()
        if running_now >= settings.max_concurrent_jobs_per_tenant:
            raise TenantLimitExceeded(
                f"Too many jobs already in flight ({settings.max_concurrent_jobs_per_tenant} "
                "max at once). Wait for one to finish or fail before starting another."
            )
