"""Real DB, mocked only at the actual YouTube API call boundary (same
approach test_youtube_upload_provider.py uses) — proves a tenant can upload
their own already-made video (skipping the AI pipeline entirely) and either
publish it immediately or schedule it, and that YouTube's own native
publishAt scheduling is what gets used rather than any custom scheduler."""

import asyncio
import io
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.security import encrypt
from src.orchestrator.tenants import ensure_tenant
from src.providers.youtube.base import UploadResult

TENANT = uuid.uuid4()

client = TestClient(app, follow_redirects=False)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Upload Own Video Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "DELETE FROM youtube_videos WHERE job_id IN "
                    "(SELECT id FROM jobs WHERE tenant_id = :t)"
                ),
                {"t": TENANT},
            )
            await session.execute(
                text("DELETE FROM api_call_logs WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


async def _insert_channel(with_youtube: bool) -> str:
    async with service_session() as session:
        channel_id = (
            await session.execute(
                text(
                    "INSERT INTO channels (tenant_id, name, youtube_channel_id, "
                    "youtube_refresh_token_encrypted) VALUES (:t, :name, :yid, :token) "
                    "RETURNING id"
                ),
                {
                    "t": TENANT,
                    "name": "Upload Test Channel",
                    "yid": "UC_fake_channel" if with_youtube else None,
                    "token": encrypt("fake-refresh-token") if with_youtube else None,
                },
            )
        ).scalar_one()
        await session.commit()
    return str(channel_id)


async def _youtube_video_row(video_id: str) -> dict | None:
    async with service_session() as session:
        row = (
            await session.execute(
                text("SELECT * FROM youtube_videos WHERE youtube_video_id = :vid"),
                {"vid": video_id},
            )
        ).mappings().first()
        return dict(row) if row else None


def _fake_video_file() -> dict:
    return {"video": ("my_video.mp4", io.BytesIO(b"fake-mp4-bytes"), "video/mp4")}


def test_upload_publishes_immediately_with_chosen_privacy() -> None:
    channel_id = asyncio.run(_insert_channel(with_youtube=True))
    mock_upload = AsyncMock(
        return_value=UploadResult(video_id="vid_now_1", url="https://youtu.be/vid_now_1")
    )

    with patch(
        "src.dashboard_tenant.router.YouTubeAPIProvider.upload_video", new=mock_upload
    ):
        resp = client.post(
            f"/dashboard/channels/{channel_id}/upload-video",
            data={
                "title": "My Own Video",
                "description": "A video I made myself",
                "tags": "vlog, personal",
                "privacy_status": "unlisted",
            },
            files=_fake_video_file(),
        )

    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == "/dashboard/channels?uploaded=1"

    mock_upload.assert_awaited_once()
    call_kwargs = mock_upload.await_args.kwargs
    assert call_kwargs["title"] == "My Own Video"
    assert call_kwargs["tags"] == ["vlog", "personal"]
    assert call_kwargs["privacy_status"] == "unlisted"
    assert call_kwargs["publish_at"] is None

    row = asyncio.run(_youtube_video_row("vid_now_1"))
    assert row is not None
    assert row["status"] == "uploaded"
    assert row["scheduled_publish_at"] is None


def test_upload_with_schedule_passes_publish_at_and_records_it() -> None:
    channel_id = asyncio.run(_insert_channel(with_youtube=True))
    mock_upload = AsyncMock(
        return_value=UploadResult(video_id="vid_sched_1", url="https://youtu.be/vid_sched_1")
    )

    with patch(
        "src.dashboard_tenant.router.YouTubeAPIProvider.upload_video", new=mock_upload
    ):
        resp = client.post(
            f"/dashboard/channels/{channel_id}/upload-video",
            data={
                "title": "Scheduled Video",
                "publish_at_utc": "2026-09-01T12:00:00.000Z",
            },
            files=_fake_video_file(),
        )

    assert resp.status_code == 303, resp.text

    call_kwargs = mock_upload.await_args.kwargs
    assert call_kwargs["publish_at"] == "2026-09-01T12:00:00.000Z"

    row = asyncio.run(_youtube_video_row("vid_sched_1"))
    assert row is not None
    assert row["status"] == "scheduled"
    assert row["scheduled_publish_at"] is not None


def test_upload_rejected_without_youtube_connected() -> None:
    channel_id = asyncio.run(_insert_channel(with_youtube=False))
    mock_upload = AsyncMock()

    with patch(
        "src.dashboard_tenant.router.YouTubeAPIProvider.upload_video", new=mock_upload
    ):
        resp = client.post(
            f"/dashboard/channels/{channel_id}/upload-video",
            data={"title": "Should Not Upload"},
            files=_fake_video_file(),
        )

    assert resp.status_code == 303
    assert "job_error" in resp.headers["location"]
    mock_upload.assert_not_awaited()
