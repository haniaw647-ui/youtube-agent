from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.security import decrypt
from src.orchestrator.storage import get_storage_provider
from src.orchestrator.youtube_quota import (
    THUMBNAIL_SET_COST_UNITS,
    UPLOAD_COST_UNITS,
    QuotaExceededError,
    release_quota_reservation,
    reserve_quota_or_raise,
)
from src.providers.storage.base import StorageProvider
from src.providers.youtube.youtube_api import YouTubeAPIProvider

# A human has already reviewed this in final_qa's approval gate, but
# defaulting a brand-new automated pipeline's uploads to fully public
# immediately is the wrong failure mode to risk — a channel can override via
# provider_config once they trust it.
DEFAULT_PRIVACY_STATUS = "private"


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text(
                        "SELECT channel_id, tenant_id, title, description, tags "
                        "FROM jobs WHERE id = :id"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .one()
        )
        channel = (
            (
                await session.execute(
                    text(
                        "SELECT youtube_channel_id, youtube_refresh_token_encrypted, "
                        "provider_config FROM channels WHERE id = :id"
                    ),
                    {"id": job_row["channel_id"]},
                )
            )
            .mappings()
            .one()
        )
        video_asset = (
            (
                await session.execute(
                    text(
                        "SELECT storage_path FROM assets WHERE job_id = :job_id "
                        "AND type = 'video_final' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )
        thumbnail_asset = (
            (
                await session.execute(
                    text(
                        "SELECT storage_path FROM assets WHERE job_id = :job_id "
                        "AND type = 'thumbnail' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )

    if not channel["youtube_refresh_token_encrypted"]:
        raise RuntimeError(
            f"Channel {job_row['channel_id']} has no connected YouTube account — the "
            "tenant needs to connect one via /channels/{channel_id}/youtube/connect first."
        )
    if video_asset is None:
        raise RuntimeError(f"No final video asset found for job {job_id}")

    upload_cost = UPLOAD_COST_UNITS + (THUMBNAIL_SET_COST_UNITS if thumbnail_asset else 0)
    try:
        # Atomic check-and-record (Phase 10) — the old two-step "check, then
        # separately record after upload" had a real race under concurrency:
        # two uploads landing at nearly the same instant could both pass the
        # check before either recorded usage, breaching the shared ceiling.
        # See tests/test_quota_concurrency.py.
        reservation_id = await reserve_quota_or_raise(
            tenant_id, job_id, "videos.insert", upload_cost
        )
    except QuotaExceededError as e:
        raise RuntimeError(
            "Platform-wide YouTube API daily quota would be exceeded by this upload — "
            "failing loudly here rather than letting Google's API reject it later with a "
            "less actionable error. See ARCHITECTURE.md §9: request a quota increase, or "
            "this will clear at UTC midnight."
        ) from e

    try:
        storage = get_storage_provider()
        video_bytes = await storage.download_bytes(
            StorageProvider.key_from_storage_path(video_asset["storage_path"])
        )
        thumbnail_bytes = None
        if thumbnail_asset:
            thumbnail_bytes = await storage.download_bytes(
                StorageProvider.key_from_storage_path(thumbnail_asset["storage_path"])
            )

        refresh_token = decrypt(channel["youtube_refresh_token_encrypted"])
        privacy_status = (channel["provider_config"] or {}).get(
            "youtube_privacy_status", DEFAULT_PRIVACY_STATUS
        )

        provider = YouTubeAPIProvider()
        result = await provider.upload_video(
            refresh_token=refresh_token,
            video_bytes=video_bytes,
            title=job_row["title"] or "Untitled",
            description=job_row["description"] or "",
            tags=job_row["tags"] or [],
            privacy_status=privacy_status,
            thumbnail_bytes=thumbnail_bytes,
        )
    except Exception:
        # The reservation was speculative — release it so a failed attempt
        # doesn't permanently cost real quota that was never actually spent.
        await release_quota_reservation(reservation_id)
        raise

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO youtube_videos "
                "(tenant_id, job_id, channel_id, youtube_video_id, url, uploaded_at, status) "
                "VALUES (:tenant_id, :job_id, :channel_id, :video_id, :url, now(), 'uploaded')"
            ),
            {
                "tenant_id": job_row["tenant_id"],
                "job_id": job_id,
                "channel_id": job_row["channel_id"],
                "video_id": result.video_id,
                "url": result.url,
            },
        )
        await session.commit()

    return {"video_id": result.video_id, "url": result.url, "privacy_status": privacy_status}
