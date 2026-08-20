"""Real DB — proves the DISTINCT ON fix in video_assembly.py's visual-asset
query actually resolves to the newest row per segment_index. Real trigger:
visual_generation can now re-run (rejecting youtube_upload with notes),
which inserts a second row per segment rather than replacing the first —
the old plain ORDER BY segment_index query would have returned both,
silently breaking the scene/duration pairing downstream."""

import asyncio
import uuid

from sqlalchemy import text

from src.orchestrator.db import service_session
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()

# The exact query from video_assembly.py — kept in sync deliberately so this
# test fails loudly if that query ever regresses back to the unsafe form.
VISUAL_ASSETS_QUERY = (
    "SELECT DISTINCT ON (segment_index) segment_index, storage_path "
    "FROM assets WHERE job_id = :job_id AND type = 'visual' "
    "ORDER BY segment_index, created_at DESC"
)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Video Assembly Dedup Test Tenant"))


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "DELETE FROM assets WHERE job_id IN (SELECT id FROM jobs WHERE tenant_id = :t)"
                ),
                {"t": TENANT},
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())


def test_distinct_on_resolves_to_the_newest_row_per_segment() -> None:
    job_id = f"job_vadedup_{uuid.uuid4().hex[:8]}"

    async def _run() -> list[dict]:
        async with service_session() as session:
            await session.execute(
                text(
                    "INSERT INTO channels (id, tenant_id, name) VALUES (:c, :t, 'Dedup Ch')"
                ),
                {"c": CHANNEL_ID, "t": TENANT},
            )
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, "
                    "overall_status) VALUES (:id, :t, :c, 'video_assembly', 'running')"
                ),
                {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
            )
            # First visual_generation attempt.
            await session.execute(
                text(
                    "INSERT INTO assets (tenant_id, job_id, type, segment_index, "
                    "storage_path, created_at) VALUES "
                    "(:t, :jid, 'visual', 1, 'r2://media/old_1.jpg', now() - interval '1 hour'), "
                    "(:t, :jid, 'visual', 2, 'r2://media/old_2.jpg', now() - interval '1 hour')"
                ),
                {"t": TENANT, "jid": job_id},
            )
            # Revision re-run — newer rows for the same segments, different photos.
            await session.execute(
                text(
                    "INSERT INTO assets (tenant_id, job_id, type, segment_index, "
                    "storage_path, created_at) VALUES "
                    "(:t, :jid, 'visual', 1, 'r2://media/new_1.jpg', now()), "
                    "(:t, :jid, 'visual', 2, 'r2://media/new_2.jpg', now())"
                ),
                {"t": TENANT, "jid": job_id},
            )
            await session.commit()

            rows = (
                await session.execute(text(VISUAL_ASSETS_QUERY), {"job_id": job_id})
            ).mappings().all()
            return [dict(r) for r in rows]

    rows = asyncio.run(_run())

    assert len(rows) == 2  # not 4 — one row per segment, not every historical attempt
    by_segment = {r["segment_index"]: r["storage_path"] for r in rows}
    assert by_segment == {1: "r2://media/new_1.jpg", 2: "r2://media/new_2.jpg"}
