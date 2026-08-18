"""Real, unmocked — proves DELETE /channels/{id} (and the dashboard's
POST /channels/{id}/delete) actually walk the full dependency graph. None of
the FKs from jobs/topics/youtube_videos/etc. back to channels cascade at the
schema level, so a naive `DELETE FROM channels` would just hit a foreign key
violation the moment any real pipeline data existed for that channel — which
is every channel that's ever actually been used. This seeds one row in every
dependent table (including the jobs.topic_id <-> topics.job_id cycle) against
real production Postgres and confirms the whole tree is gone afterward.
"""

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.supabase_auth import get_current_tenant_id
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()

client = TestClient(app)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Channel Delete Test Tenant"))
    app.dependency_overrides[get_current_tenant_id] = lambda: TENANT
    app.dependency_overrides[require_tenant] = lambda: TENANT


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


async def _seed_full_dependency_tree(channel_id: str, job_id: str) -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :cid, 'topic_generation', 'running')"
            ),
            {"id": job_id, "t": TENANT, "cid": channel_id},
        )
        topic_id = (
            await session.execute(
                text(
                    "INSERT INTO topics (channel_id, job_id, title, tenant_id) "
                    "VALUES (:cid, :jid, 'Test Topic', :t) RETURNING id"
                ),
                {"cid": channel_id, "jid": job_id, "t": TENANT},
            )
        ).scalar_one()
        # Close the jobs.topic_id <-> topics.job_id cycle deliberately.
        await session.execute(
            text("UPDATE jobs SET topic_id = :tid WHERE id = :jid"),
            {"tid": topic_id, "jid": job_id},
        )
        await session.execute(
            text(
                "INSERT INTO scripts (job_id, content, tenant_id) VALUES (:jid, 'script', :t)"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.execute(
            text(
                "INSERT INTO assets (job_id, type, storage_path, tenant_id) "
                "VALUES (:jid, 'voice_over', 's3://x', :t)"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, stage, tenant_id) "
                "VALUES (:jid, 'topic_generation', :t)"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.execute(
            text(
                "INSERT INTO api_call_logs (job_id, provider, status, tenant_id) "
                "VALUES (:jid, 'anthropic', 'success', :t)"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.execute(
            text(
                "INSERT INTO approvals (job_id, stage, tenant_id) "
                "VALUES (:jid, 'youtube_upload', :t)"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.execute(
            text(
                "INSERT INTO notifications_sent (job_id, message_type, status, tenant_id) "
                "VALUES (:jid, 'success', 'sent', :t)"
            ),
            {"jid": job_id, "t": TENANT},
        )
        video_id = (
            await session.execute(
                text(
                    "INSERT INTO youtube_videos (job_id, channel_id, tenant_id) "
                    "VALUES (:jid, :cid, :t) RETURNING id"
                ),
                {"jid": job_id, "cid": channel_id, "t": TENANT},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO analytics_snapshots (youtube_video_id, tenant_id) VALUES (:vid, :t)"
            ),
            {"vid": video_id, "t": TENANT},
        )
        await session.commit()


def test_delete_channel_removes_the_full_dependency_tree() -> None:
    resp = client.post("/channels", json={"name": "Cascade Delete Test Channel"})
    assert resp.status_code == 200, resp.text
    channel_id = resp.json()["id"]
    job_id = f"job_delete_test_{uuid.uuid4().hex[:8]}"

    asyncio.run(_seed_full_dependency_tree(channel_id, job_id))

    resp = client.delete(f"/channels/{channel_id}")
    assert resp.status_code == 204, resp.text

    async def _assert_all_gone() -> None:
        async with service_session() as session:
            for table, col, val in [
                ("channels", "id", channel_id),
                ("jobs", "id", job_id),
                ("topics", "job_id", job_id),
                ("scripts", "job_id", job_id),
                ("assets", "job_id", job_id),
                ("job_stages", "job_id", job_id),
                ("api_call_logs", "job_id", job_id),
                ("approvals", "job_id", job_id),
                ("notifications_sent", "job_id", job_id),
                ("youtube_videos", "job_id", job_id),
            ]:
                remaining = (
                    await session.execute(
                        text(f"SELECT count(*) FROM {table} WHERE {col} = :v"), {"v": val}
                    )
                ).scalar_one()
                assert remaining == 0, f"{table} still has rows referencing {val}"

    asyncio.run(_assert_all_gone())


def test_delete_channel_404s_for_a_channel_that_does_not_exist() -> None:
    resp = client.delete(f"/channels/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_dashboard_delete_route_cascades_and_redirects() -> None:
    resp = client.post("/channels", json={"name": "Dashboard Cascade Delete Test Channel"})
    channel_id = resp.json()["id"]
    job_id = f"job_dash_delete_test_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_full_dependency_tree(channel_id, job_id))

    resp = client.post(f"/dashboard/channels/{channel_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/channels?deleted=1"

    async def _assert_gone() -> None:
        async with service_session() as session:
            remaining = (
                await session.execute(
                    text("SELECT count(*) FROM channels WHERE id = :id"), {"id": channel_id}
                )
            ).scalar_one()
            assert remaining == 0

    asyncio.run(_assert_gone())
