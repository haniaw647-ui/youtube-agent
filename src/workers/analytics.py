import json
from datetime import datetime

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.security import decrypt
from src.orchestrator.timeutil import utcnow_naive
from src.providers.youtube.youtube_api import YouTubeAPIProvider

# ARCHITECTURE.md / IMPLEMENTATION_PLAN.md Phase 9: +1/+7/+30 day snapshots.
SNAPSHOT_DAYS = (1, 7, 30)


def due_snapshot_days(
    uploaded_at: datetime, already_snapshotted_days: set[int], now: datetime
) -> list[int]:
    """Pure, no I/O — which of the fixed day-marks are both reached (video is
    old enough) and not yet captured. Idempotent by design: if the periodic
    task misses a run, the next one just catches up on whatever's still due
    rather than double-snapshotting or losing a mark."""
    age_days = (now - uploaded_at).days
    return [day for day in SNAPSHOT_DAYS if age_days >= day and day not in already_snapshotted_days]


async def pull_due_snapshots() -> dict:
    provider = YouTubeAPIProvider()
    now = utcnow_naive()

    async with service_session() as session:
        videos = (
            (
                await session.execute(
                    text(
                        "SELECT v.id, v.tenant_id, v.youtube_video_id, v.uploaded_at, "
                        "c.youtube_refresh_token_encrypted "
                        "FROM youtube_videos v "
                        "JOIN channels c ON c.id = v.channel_id "
                        "WHERE v.uploaded_at IS NOT NULL AND v.youtube_video_id IS NOT NULL"
                    )
                )
            )
            .mappings()
            .all()
        )

    videos_checked = 0
    snapshots_taken = 0
    errors: list[str] = []

    for video in videos:
        videos_checked += 1
        if not video["youtube_refresh_token_encrypted"]:
            continue

        async with service_session() as session:
            existing = (
                (
                    await session.execute(
                        text(
                            "SELECT metrics FROM analytics_snapshots WHERE youtube_video_id = :id"
                        ),
                        {"id": video["id"]},
                    )
                )
                .mappings()
                .all()
            )
        already = {row["metrics"].get("day") for row in existing if row["metrics"].get("day")}

        due = due_snapshot_days(video["uploaded_at"], already, now)
        if not due:
            continue

        try:
            refresh_token = decrypt(video["youtube_refresh_token_encrypted"])
            stats = await provider.get_video_stats(refresh_token, video["youtube_video_id"])
        except Exception as exc:  # noqa: BLE001 - one video's failure shouldn't abort the batch
            errors.append(f"{video['youtube_video_id']}: {exc}")
            continue

        async with service_session() as session:
            for day in due:
                await session.execute(
                    text(
                        "INSERT INTO analytics_snapshots (tenant_id, youtube_video_id, metrics) "
                        "VALUES (:tenant_id, :video_id, :metrics)"
                    ),
                    {
                        "tenant_id": video["tenant_id"],
                        "video_id": video["id"],
                        "metrics": json.dumps(
                            {
                                "day": day,
                                "views": stats.view_count,
                                "likes": stats.like_count,
                                "comments": stats.comment_count,
                            }
                        ),
                    },
                )
                snapshots_taken += 1
            await session.commit()

    return {"videos_checked": videos_checked, "snapshots_taken": snapshots_taken, "errors": errors}
