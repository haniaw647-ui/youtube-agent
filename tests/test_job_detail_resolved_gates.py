"""Real DB — proves /dashboard/jobs/{job_id} shows a resolved approval gate
(topic_scoring/script_qa/youtube_upload) as its actual decision instead of
"not_started". approve_submit deletes the gate's job_stages row the moment
it's resolved (the outcome lives in `approvals`, not `job_stages`), so the
job_detail route previously fell back to a "not_started" placeholder for a
gate that genuinely ran and was approved — misleading on the timeline."""

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
    asyncio.run(ensure_tenant(TENANT, "Job Detail Gate Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Gate Ch')"),
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
                text("DELETE FROM job_stages WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


def test_a_resolved_gate_shows_its_decision_not_not_started() -> None:
    job_id = f"job_gatedone_{uuid.uuid4().hex[:8]}"

    async def _seed() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, "
                    "overall_status) VALUES (:id, :t, :c, 'research', 'running')"
                ),
                {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
            )
            # Mirrors what approve_submit does: the awaiting_approval job_stages
            # row is gone, and the outcome lives only in `approvals`.
            await session.execute(
                text(
                    "INSERT INTO approvals (job_id, tenant_id, stage, decision, "
                    "resolved_at, resolved_by) VALUES "
                    "(:jid, :t, 'topic_scoring', 'approved', now(), 'tenant')"
                ),
                {"jid": job_id, "t": TENANT},
            )
            await session.commit()

    asyncio.run(_seed())

    resp = client.get(f"/dashboard/jobs/{job_id}")

    assert resp.status_code == 200
    assert "not_started" not in resp.text.split('>topic_scoring<')[1][:200]
    assert "approved" in resp.text


def test_a_stage_that_never_ran_still_shows_not_started() -> None:
    job_id = f"job_gatepending_{uuid.uuid4().hex[:8]}"

    async def _seed() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, "
                    "overall_status) VALUES (:id, :t, :c, 'topic_generation', 'running')"
                ),
                {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
            )
            await session.commit()

    asyncio.run(_seed())

    resp = client.get(f"/dashboard/jobs/{job_id}")

    assert resp.status_code == 200
    assert "not_started" in resp.text
