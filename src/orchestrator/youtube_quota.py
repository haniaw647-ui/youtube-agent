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


class QuotaExceededError(Exception):
    pass


# Arbitrary fixed key for the platform-wide quota lock — any int works, it
# just has to be the same constant everywhere this is taken.
_QUOTA_LOCK_KEY = 837_412_009


async def reserve_quota_or_raise(
    tenant_id: str, job_id: str, endpoint: str, units: int, status: str = "success"
) -> uuid.UUID:
    """Atomic check-and-record: would_exceed_daily_quota() + record_quota_usage()
    used to be two separate round trips, which is a real TOCTOU race under
    concurrency — two uploads landing at nearly the same instant could both
    pass the check before either recorded its usage, breaching the shared
    ceiling (caught by tests/test_quota_concurrency.py, Phase 10). A Postgres
    session-level advisory lock serializes every reservation platform-wide —
    cheap here since uploads are rare/high-latency, not a hot path.

    Returns the reservation's row id so the caller can delete it (see
    release_quota_reservation) if the upload it was reserved for ends up
    failing, rather than a failed attempt permanently costing real quota."""
    async with service_session() as session:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": _QUOTA_LOCK_KEY}
        )
        used = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(quota_units), 0) FROM api_call_logs "
                    "WHERE provider = :provider AND created_at >= :since"
                ),
                {
                    "provider": PROVIDER,
                    "since": utcnow_naive().replace(hour=0, minute=0, second=0, microsecond=0),
                },
            )
        ).scalar_one()
        if used + units > DEFAULT_DAILY_QUOTA:
            raise QuotaExceededError(
                f"Platform-wide YouTube API daily quota would be exceeded: "
                f"{used} used + {units} requested > {DEFAULT_DAILY_QUOTA}"
            )

        reservation_id = (
            await session.execute(
                text(
                    "INSERT INTO api_call_logs "
                    "(tenant_id, job_id, stage, provider, endpoint, status, quota_units) "
                    "VALUES (:tenant_id, :job_id, 'youtube_upload', :provider, :endpoint, "
                    " :status, :units) RETURNING id"
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
        ).scalar_one()
        await session.commit()
    return reservation_id


async def release_quota_reservation(reservation_id: uuid.UUID) -> None:
    """Deletes a reservation made by reserve_quota_or_raise — call this if
    the upload it was reserved for didn't actually happen, so a failed
    attempt doesn't permanently cost real quota."""
    async with service_session() as session:
        await session.execute(
            text("DELETE FROM api_call_logs WHERE id = :id"), {"id": reservation_id}
        )
        await session.commit()


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
