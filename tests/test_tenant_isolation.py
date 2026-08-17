"""Proves tenant isolation end-to-end through the real API layer against the
real (Supabase-hosted) Postgres RLS policies — not mocked, not app-logic-only.
Two synthetic tenant identities are injected via FastAPI's dependency override
(bypassing the Supabase Auth email-confirmation round trip, which is a
separate, already-verified concern — see src/orchestrator/supabase_auth.py).
Everything downstream of "who is the caller" exercises the real RLS-enforced
tenant_session() from src/orchestrator/db.py.
"""

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

import src.orchestrator.routes.jobs as jobs_module
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.supabase_auth import get_current_tenant_id
from src.orchestrator.tenants import ensure_tenant

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()

client = TestClient(app)


def _as(tenant_id: uuid.UUID) -> None:
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant_id


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT_A, "Isolation Test Tenant A"))
    asyncio.run(ensure_tenant(TENANT_B, "Isolation Test Tenant B"))
    # Job creation would otherwise try to reach a Celery broker; this test is
    # about API/RLS authorization, not pipeline execution, so stub it out.
    jobs_module.enqueue_stage = lambda *args, **kwargs: None


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            for tid in (TENANT_A, TENANT_B):
                await session.execute(
                    text("DELETE FROM tenant_api_keys WHERE tenant_id = :t"), {"t": tid}
                )
                await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": tid})
                await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": tid})
                await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


def test_tenant_cannot_read_or_write_another_tenants_data() -> None:
    _as(TENANT_A)
    resp = client.post("/channels", json={"name": "A's Channel"})
    assert resp.status_code == 200, resp.text
    channel_a = resp.json()["id"]

    resp = client.post(f"/channels/{channel_a}/jobs")
    assert resp.status_code == 200, resp.text
    job_a = resp.json()["id"]

    _as(TENANT_B)
    resp = client.post("/channels", json={"name": "B's Channel"})
    assert resp.status_code == 200, resp.text
    channel_b = resp.json()["id"]

    # B's channel list contains only B's channel.
    resp = client.get("/channels")
    channel_ids = [c["id"] for c in resp.json()]
    assert channel_a not in channel_ids, "ISOLATION BREACH: tenant B saw tenant A's channel"
    assert channel_b in channel_ids

    # B cannot fetch A's job by ID — RLS makes it invisible, not just "forbidden".
    resp = client.get(f"/jobs/{job_a}")
    assert resp.status_code == 404

    # B cannot create a job against A's channel_id — the channel is invisible under
    # B's RLS-scoped session, so this must 404 rather than silently succeeding.
    resp = client.post(f"/channels/{channel_a}/jobs")
    assert resp.status_code == 404

    # B's own job list never contains A's job.
    resp = client.get("/jobs")
    assert job_a not in [j["id"] for j in resp.json()]

    # Sanity check the other direction too: A still sees their own job.
    _as(TENANT_A)
    resp = client.get("/jobs")
    assert job_a in [j["id"] for j in resp.json()]


def test_tenant_api_keys_are_isolated() -> None:
    _as(TENANT_A)
    resp = client.put("/tenant/api-keys", json={"provider": "anthropic", "api_key": "sk-a-secret"})
    assert resp.status_code == 200, resp.text

    _as(TENANT_B)
    resp = client.get("/tenant/api-keys")
    assert resp.status_code == 200
    assert resp.json() == [], "ISOLATION BREACH: tenant B saw tenant A's API key"
