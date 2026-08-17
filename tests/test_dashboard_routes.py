"""Proves the tenant-facing dashboard pages added on top of the existing
approvals/channels dashboard actually work against the real (Supabase-hosted)
Postgres — not mocked. Auth is injected via FastAPI's dependency override on
`require_tenant`, the same pattern test_tenant_isolation.py uses for
`get_current_tenant_id`: bypassing the Supabase Auth cookie round trip (a
separate, already-verified concern) while exercising every real DB write and
read downstream of it.
"""

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

import src.dashboard_tenant.router as dashboard_router
from src.dashboard_tenant.auth import require_tenant
from src.models.pipeline import PIPELINE_STAGES
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()

client = TestClient(app, follow_redirects=False)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Dashboard Route Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT
    # Job creation would otherwise try to reach a Celery broker; this test is
    # about the dashboard's DB reads/writes and rendering, not pipeline execution.
    dashboard_router.enqueue_stage = lambda *args, **kwargs: None


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text("DELETE FROM tenant_api_keys WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(
                text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


def test_create_channel_via_dashboard_form_and_list_it() -> None:
    resp = client.post(
        "/dashboard/channels/create",
        data={"name": "Dashboard Test Channel", "niche": "testing", "language": "en"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/channels"

    resp = client.get("/dashboard/channels")
    assert resp.status_code == 200
    assert "Dashboard Test Channel" in resp.text


def test_create_job_from_channel_and_view_pipeline_progress() -> None:
    resp = client.get("/dashboard/channels")
    # Pull the channel id back out of the create form's follow-up (real row, real query).
    async def _get_channel_id() -> str:
        async with service_session() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id FROM channels WHERE tenant_id = :t "
                            "AND name = 'Dashboard Test Channel'"
                        ),
                        {"t": TENANT},
                    )
                )
                .mappings()
                .one()
            )
        return str(row["id"])

    channel_id = asyncio.run(_get_channel_id())

    resp = client.post(f"/dashboard/channels/{channel_id}/jobs/create")
    assert resp.status_code == 303, resp.text
    job_url = resp.headers["location"]
    assert job_url.startswith("/dashboard/jobs/job_")

    resp = client.get("/dashboard/jobs")
    assert resp.status_code == 200
    job_id = job_url.rsplit("/", 1)[-1]
    assert job_id in resp.text
    assert "Dashboard Test Channel" in resp.text

    resp = client.get(job_url)
    assert resp.status_code == 200
    # Every one of the 15 fixed pipeline stages must render, run or not —
    # this is the whole point of the job detail page.
    for stage in PIPELINE_STAGES:
        assert stage in resp.text
    assert "not_started" in resp.text  # nothing has actually run (no worker in this test)


def test_api_keys_page_add_and_mask() -> None:
    resp = client.post(
        "/dashboard/api-keys", data={"provider": "anthropic", "api_key": "sk-ant-test-secret-value"}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard/api-keys?saved=anthropic"

    resp = client.get("/dashboard/api-keys")
    assert resp.status_code == 200
    assert "sk-ant-test-secret-value" not in resp.text  # never render the raw key
    assert "value" in resp.text.lower()  # masked suffix from src/orchestrator/security.mask
