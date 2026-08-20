"""Real DB — proves /dashboard/jobs/{job_id}/delete actually removes a job
and its dependent rows (no FK violations from _JOB_DEPENDENT_TABLES not
being walked in the right order), and refuses to delete a job that's still
'running' so an in-flight Celery task doesn't hit FK violations writing to
a job_id that no longer exists."""

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()

client = TestClient(app)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Job Delete Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Delete Ch')"),
            {"id": CHANNEL_ID, "t": TENANT},
        )
        await session.commit()


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text("DELETE FROM approvals WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(
                text("DELETE FROM assets WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(
                text("DELETE FROM job_stages WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(
                text(
                    "UPDATE jobs SET topic_id = NULL WHERE tenant_id = :t"
                ),
                {"t": TENANT},
            )
            await session.execute(text("DELETE FROM topics WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


async def _seed_job(job_id: str, overall_status: str) -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :c, 'final_qa', :status)"
            ),
            {"id": job_id, "t": TENANT, "c": CHANNEL_ID, "status": overall_status},
        )
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status) "
                "VALUES (:jid, :t, 'video_assembly', 'done')"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.execute(
            text(
                "INSERT INTO assets (tenant_id, job_id, type, storage_path) "
                "VALUES (:t, :jid, 'video_final', 'r2://media/x.mp4')"
            ),
            {"t": TENANT, "jid": job_id},
        )
        await session.execute(
            text(
                "INSERT INTO approvals (job_id, tenant_id, stage, decision, resolved_at, "
                "resolved_by) VALUES (:jid, :t, 'script_qa', 'approved', now(), 'tenant')"
            ),
            {"jid": job_id, "t": TENANT},
        )
        await session.commit()


def test_deleting_a_done_job_removes_it_and_its_dependent_rows() -> None:
    job_id = f"job_del_done_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, "done"))

    resp = client.post(f"/dashboard/jobs/{job_id}/delete", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/jobs"

    async def _check() -> tuple:
        async with service_session() as session:
            job = (
                await session.execute(text("SELECT 1 FROM jobs WHERE id = :id"), {"id": job_id})
            ).first()
            stages = (
                await session.execute(
                    text("SELECT 1 FROM job_stages WHERE job_id = :id"), {"id": job_id}
                )
            ).first()
            assets = (
                await session.execute(
                    text("SELECT 1 FROM assets WHERE job_id = :id"), {"id": job_id}
                )
            ).first()
            approvals = (
                await session.execute(
                    text("SELECT 1 FROM approvals WHERE job_id = :id"), {"id": job_id}
                )
            ).first()
            return job, stages, assets, approvals

    job, stages, assets, approvals = asyncio.run(_check())
    assert job is None
    assert stages is None
    assert assets is None
    assert approvals is None


def test_deleting_a_running_job_is_refused() -> None:
    job_id = f"job_del_running_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, "running"))

    resp = client.post(f"/dashboard/jobs/{job_id}/delete", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard/jobs?error=")

    async def _still_exists() -> bool:
        async with service_session() as session:
            row = (
                await session.execute(text("SELECT 1 FROM jobs WHERE id = :id"), {"id": job_id})
            ).first()
            return row is not None

    assert asyncio.run(_still_exists())


def test_deleting_a_nonexistent_job_404s() -> None:
    resp = client.post("/dashboard/jobs/job_does_not_exist/delete", follow_redirects=False)
    assert resp.status_code == 404
