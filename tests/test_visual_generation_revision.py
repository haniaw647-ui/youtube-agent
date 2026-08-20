"""Real DB, mocked at the provider/storage/network boundary — proves
visual_generation requests more candidates and picks a different one when
re-run because a tenant rejected youtube_upload with notes (e.g. "change
the photos"), rather than deterministically re-fetching the exact same
image. Search results are deterministic for the same query, so re-running
unchanged would silently produce byte-identical photos."""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.tenants import ensure_tenant
from src.providers.visual.base import VisualResult
from src.workers.stages.visual_generation import run

TENANT = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Visual Revision Test Tenant"))
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO channels (id, tenant_id, name) VALUES (:c, :t, 'Visual Rev Ch')"
            ),
            {"c": CHANNEL_ID, "t": TENANT},
        )
        await session.commit()


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
                text("DELETE FROM approvals WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(
                text("DELETE FROM job_stages WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())


async def _seed_job_with_script(job_id: str) -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :c, 'visual_generation', 'running')"
            ),
            {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
        )
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status, output_ref) "
                "VALUES (:id, :t, 'script_writing', 'done', :output_ref)"
            ),
            {
                "id": job_id,
                "t": TENANT,
                "output_ref": '{"segments": [{"scene": 1, "narration": "n", '
                '"visual_note": "a mountain lake"}]}',
            },
        )
        await session.commit()


async def _mark_youtube_upload_rejected(job_id: str, notes: str) -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO approvals (job_id, tenant_id, stage, decision, notes, resolved_at) "
                "VALUES (:jid, :t, 'youtube_upload', 'rejected', :notes, now())"
            ),
            {"jid": job_id, "t": TENANT, "notes": notes},
        )
        await session.commit()


def _results(n: int) -> list[VisualResult]:
    return [
        VisualResult(
            url=f"https://example.com/{i}.jpg",
            photographer="p",
            source="pexels",
            license_type="pexels-free-commercial-use",
        )
        for i in range(n)
    ]


def _patches(search_mock: AsyncMock):
    return (
        patch(
            "src.workers.stages.visual_generation.get_tenant_key",
            new=AsyncMock(return_value="fake-key"),
        ),
        patch("src.workers.stages.visual_generation.PexelsProvider.search", new=search_mock),
        patch(
            "src.workers.stages.visual_generation.download",
            new=AsyncMock(return_value=b"fake-image-bytes"),
        ),
        patch(
            "src.workers.stages.visual_generation.get_storage_provider",
            return_value=AsyncMock(upload_bytes=AsyncMock(return_value="r2://media/x.jpg")),
        ),
    )


def test_first_run_requests_a_single_result() -> None:
    job_id = f"job_visrev_first_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job_with_script(job_id))

    search_mock = AsyncMock(return_value=_results(1))
    patches = _patches(search_mock)
    with patches[0], patches[1], patches[2], patches[3]:
        asyncio.run(run(job_id, str(TENANT)))

    search_mock.assert_awaited_once_with("a mountain lake", count=1)


def test_revision_requests_more_results_and_picks_a_different_one() -> None:
    job_id = f"job_visrev_redo_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job_with_script(job_id))
    asyncio.run(_mark_youtube_upload_rejected(job_id, "change the photos"))

    candidates = _results(3)
    search_mock = AsyncMock(return_value=candidates)
    patches = _patches(search_mock)
    with patches[0], patches[1], patches[2] as mock_download, patches[3]:
        asyncio.run(run(job_id, str(TENANT)))

    search_mock.assert_awaited_once_with("a mountain lake", count=3)
    # Picked the last candidate, not the first — genuinely different from
    # whatever a first run (count=1, index 0) would have fetched.
    mock_download.assert_awaited_once_with(candidates[-1].url)
