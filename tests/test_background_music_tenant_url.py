"""Real DB, ffmpeg mocked at the same boundary voice_over's placeholder test
uses — proves background_music prefers a tenant-supplied music URL over the
synthesized placeholder tone, stamps it with a distinct (non-"placeholder")
license_type so final_qa's audit doesn't force a review it doesn't need, and
falls back cleanly to the placeholder tone if the URL download fails."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.tenants import ensure_tenant
from src.workers.stages.background_music import (
    PLACEHOLDER_LICENSE_TYPE,
    TENANT_PROVIDED_LICENSE_TYPE,
    run,
)

TENANT = uuid.uuid4()


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Background Music URL Test Tenant"))


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "DELETE FROM assets WHERE job_id IN (SELECT id FROM jobs WHERE tenant_id = :t)"
                ),
                {"t": TENANT},
            )
            await session.execute(
                text("DELETE FROM job_stages WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())


async def _seed_job(job_id: str, music_url: str | None) -> None:
    async with service_session() as session:
        channel_id = (
            await session.execute(
                text(
                    "INSERT INTO channels (tenant_id, name, approval_gates, background_music_url) "
                    "VALUES (:t, 'Music URL Test Channel', '{}', :music_url) RETURNING id"
                ),
                {"t": TENANT, "music_url": music_url},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :cid, 'background_music', 'running')"
            ),
            {"id": job_id, "t": TENANT, "cid": channel_id},
        )
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status, output_ref) "
                "VALUES (:id, :t, 'subtitle_burn_in', 'done', :output_ref)"
            ),
            {"id": job_id, "t": TENANT, "output_ref": '{"storage_path": "r2://media/fake.mp4"}'},
        )
        await session.commit()


async def _asset_row(job_id: str) -> dict:
    async with service_session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT license_type FROM assets WHERE job_id = :jid AND type = 'video_final'"
                ),
                {"jid": job_id},
            )
        ).mappings().one()
        return dict(row)


async def _fake_generate_placeholder_music(duration: float, output_path: str) -> None:
    with open(output_path, "wb") as f:
        f.write(b"fake-placeholder-music-bytes")


async def _fake_mix_background_music(video_path: str, music_path: str, output_path: str) -> None:
    with open(output_path, "wb") as f:
        f.write(b"fake-final-video-bytes")


def _patch_ffmpeg_and_storage(music_download: AsyncMock):
    generate_placeholder = AsyncMock(side_effect=_fake_generate_placeholder_music)
    mix_music = AsyncMock(side_effect=_fake_mix_background_music)
    return (
        patch(
            "src.workers.stages.background_music.get_storage_provider",
            return_value=AsyncMock(
                download_bytes=AsyncMock(return_value=b"fake-video-bytes"),
                upload_bytes=AsyncMock(return_value="r2://media/final.mp4"),
            ),
        ),
        patch(
            "src.workers.stages.background_music.probe_duration_seconds",
            new=AsyncMock(return_value=30.0),
        ),
        patch("src.workers.stages.background_music.download", new=music_download),
        patch(
            "src.workers.stages.background_music.generate_placeholder_music",
            new=generate_placeholder,
        ),
        patch("src.workers.stages.background_music.mix_background_music", new=mix_music),
    )


def test_prefers_tenant_music_url_over_placeholder() -> None:
    job_id = f"job_music_url_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, "https://example.com/track.mp3"))

    music_download = AsyncMock(return_value=b"fake-mp3-bytes")
    patches = _patch_ffmpeg_and_storage(music_download)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = asyncio.run(run(job_id, str(TENANT)))

    music_download.assert_awaited_once_with("https://example.com/track.mp3")
    assert result["license_type"] == TENANT_PROVIDED_LICENSE_TYPE
    assert "https://example.com/track.mp3" in result["attribution_text"]
    row = asyncio.run(_asset_row(job_id))
    assert row["license_type"] == TENANT_PROVIDED_LICENSE_TYPE


def test_falls_back_to_placeholder_when_no_music_url_set() -> None:
    job_id = f"job_music_none_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, None))

    music_download = AsyncMock()
    patches = _patch_ffmpeg_and_storage(music_download)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = asyncio.run(run(job_id, str(TENANT)))

    music_download.assert_not_awaited()
    assert result["license_type"] == PLACEHOLDER_LICENSE_TYPE


def test_falls_back_to_placeholder_when_download_fails() -> None:
    job_id = f"job_music_fail_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, "https://example.com/broken.mp3"))

    music_download = AsyncMock(side_effect=httpx.ConnectError("boom"))
    patches = _patch_ffmpeg_and_storage(music_download)
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = asyncio.run(run(job_id, str(TENANT)))

    music_download.assert_awaited_once()
    assert result["license_type"] == PLACEHOLDER_LICENSE_TYPE
