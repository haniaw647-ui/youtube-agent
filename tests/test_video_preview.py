"""Real, unmocked — uploads real bytes to the actual storage provider,
seeds a real assets row, and proves /dashboard/jobs/{job_id}/video-preview
serves them back correctly (full response, and a Range request), and that
a job with no video_final asset 404s instead of crashing."""

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.storage import get_storage_provider
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()
VIDEO_BYTES = b"fake-mp4-bytes-for-preview-test" * 100

client = TestClient(app)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Video Preview Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Preview Ch')"
            ),
            {"id": CHANNEL_ID, "t": TENANT},
        )
        await session.commit()


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "DELETE FROM assets WHERE job_id IN "
                    "(SELECT id FROM jobs WHERE tenant_id = :t)"
                ),
                {"t": TENANT},
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


async def _seed_job_with_video_asset(job_id: str) -> None:
    storage = get_storage_provider()
    key = f"{TENANT}/{CHANNEL_ID}/{job_id}/render/final.mp4"
    storage_path = await storage.upload_bytes(key, VIDEO_BYTES, "video/mp4")

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :c, 'youtube_upload', 'awaiting_approval')"
            ),
            {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
        )
        await session.execute(
            text(
                "INSERT INTO assets (tenant_id, job_id, type, storage_path) "
                "VALUES (:t, :jid, 'video_final', :path)"
            ),
            {"t": TENANT, "jid": job_id, "path": storage_path},
        )
        await session.commit()


def test_preview_serves_the_full_video() -> None:
    job_id = f"job_preview_full_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job_with_video_asset(job_id))

    resp = client.get(f"/dashboard/jobs/{job_id}/video-preview")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == VIDEO_BYTES


def test_preview_honors_range_requests_for_seeking() -> None:
    job_id = f"job_preview_range_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job_with_video_asset(job_id))

    resp = client.get(
        f"/dashboard/jobs/{job_id}/video-preview", headers={"Range": "bytes=10-19"}
    )

    assert resp.status_code == 206
    assert resp.content == VIDEO_BYTES[10:20]
    assert resp.headers["content-range"] == f"bytes 10-19/{len(VIDEO_BYTES)}"


def test_preview_404s_when_no_video_asset_exists() -> None:
    job_id = f"job_preview_missing_{uuid.uuid4().hex[:8]}"

    async def _seed_bare_job() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                    "VALUES (:id, :t, :c, 'video_assembly', 'running')"
                ),
                {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
            )
            await session.commit()

    asyncio.run(_seed_bare_job())

    resp = client.get(f"/dashboard/jobs/{job_id}/video-preview")
    assert resp.status_code == 404
