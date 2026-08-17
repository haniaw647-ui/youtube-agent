import uuid

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.timeutil import utcnow_naive

# Google's default per-project daily quota, and what each call actually
# costs — ARCHITECTURE.md §9. This is a platform-wide shared ceiling, not
# per-tenant, because every tenant's upload goes through the same registered
# OAuth app / Google Cloud project.
DEFAULT_DAILY_QUOTA = 10_000
UPLOAD_COST_UNITS = 1_600
THUMBNAIL_SET_COST_UNITS = 50

PROVIDER = "youtube_data_api"


async def get_todays_quota_usage() -> int:
    """Platform-wide, not tenant-scoped — the quota ceiling is shared, so the
    budget check has to see every tenant's usage, not just the caller's."""
    since = utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    async with service_session() as session:
        total = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(quota_units), 0) FROM api_call_logs "
                    "WHERE provider = :provider AND created_at >= :since"
                ),
                {"provider": PROVIDER, "since": since},
            )
        ).scalar_one()
    return int(total)


async def would_exceed_daily_quota(additional_units: int) -> bool:
    used = await get_todays_quota_usage()
    return used + additional_units > DEFAULT_DAILY_QUOTA


async def record_quota_usage(
    tenant_id: str, job_id: str, endpoint: str, units: int, status: str = "success"
) -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO api_call_logs "
                "(tenant_id, job_id, stage, provider, endpoint, status, quota_units) "
                "VALUES (:tenant_id, :job_id, 'youtube_upload', :provider, :endpoint, "
                " :status, :units)"
            ),
            {
                "tenant_id": uuid.UUID(str(tenant_id)),
                "job_id": job_id,
                "provider": PROVIDER,
                "endpoint": endpoint,
                "status": status,
                "units": units,
            },
        )
        await session.commit()
