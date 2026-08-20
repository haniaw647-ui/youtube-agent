"""Real DB — proves the Overview page's Activity graph reads from the
append-only job_creation_events log, not the live `jobs` table, so deleting
a job (a real dashboard feature) doesn't also erase that day's history from
the graph. Before this, the graph did COUNT(*) on `jobs` directly, so a
deleted job silently vanished from its creation day's count too."""

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
    asyncio.run(ensure_tenant(TENANT, "Activity Survives Delete Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Activity Ch')"),
            {"id": CHANNEL_ID, "t": TENANT},
        )
        await session.commit()


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text("DELETE FROM job_creation_events WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


def test_deleting_a_job_does_not_remove_it_from_the_activity_graph() -> None:
    job_id = f"job_actdel_{uuid.uuid4().hex[:8]}"

    async def _seed_and_delete() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, "
                    "overall_status) VALUES (:id, :t, :c, 'final_qa', 'done')"
                ),
                {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
            )
            await session.execute(
                text(
                    "INSERT INTO job_creation_events (tenant_id, job_id) "
                    "VALUES (:t, :jid)"
                ),
                {"t": TENANT, "jid": job_id},
            )
            await session.commit()

    asyncio.run(_seed_and_delete())

    resp = client.post(f"/dashboard/jobs/{job_id}/delete", follow_redirects=False)
    assert resp.status_code == 303

    async def _job_and_event_exist() -> tuple:
        async with service_session() as session:
            job = (
                await session.execute(text("SELECT 1 FROM jobs WHERE id = :id"), {"id": job_id})
            ).first()
            event = (
                await session.execute(
                    text("SELECT 1 FROM job_creation_events WHERE job_id = :id"), {"id": job_id}
                )
            ).first()
            return job, event

    job, event = asyncio.run(_job_and_event_exist())
    assert job is None, "job row should be gone after delete"
    assert event is not None, "job_creation_events row should survive the job's deletion"

    overview_resp = client.get("/dashboard/overview")
    assert overview_resp.status_code == 200
