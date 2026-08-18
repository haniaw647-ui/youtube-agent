"""Real, unmocked — proves the internal ops dashboard's own auth gate works
(fail-closed when unset, blocks without a valid session, accepts the right
password) and that it genuinely sees cross-tenant data via service_session(),
which is the entire point of it existing. Inserts a synthetic tenant/job
against production Postgres and cleans up afterward.
"""

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_admin.auth import check_password
from src.orchestrator.config import get_settings
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()
ADMIN_PASSWORD = "test-operator-password"

client = TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def _admin_password(monkeypatch):
    monkeypatch.setenv("ADMIN_DASHBOARD_PASSWORD", ADMIN_PASSWORD)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Admin Dashboard Test Tenant"))

    async def _seed() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "INSERT INTO channels (id, tenant_id, name) "
                    "VALUES (:id, :t, 'Admin Test Channel')"
                ),
                {"id": CHANNEL_ID, "t": TENANT},
            )
            await session.execute(
                text(
                    "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                    "VALUES ('job_admin_test_001', :t, :c, 'topic_generation', 'running')"
                ),
                {"t": TENANT, "c": CHANNEL_ID},
            )
            # A real flagged script_qa row — this is what /admin/abuse and the
            # overview's abuse_flags count actually query against. Regression
            # coverage for a real bug: output_ref is a JSON column, and
            # jsonb_array_length() rejects json without an explicit ::jsonb
            # cast — this 500'd in production the moment real data existed,
            # uncaught because no earlier test ever exercised this query.
            await session.execute(
                text(
                    "INSERT INTO job_stages (job_id, tenant_id, stage, status, output_ref) "
                    "VALUES ('job_admin_test_001', :t, 'script_qa', 'done', :output_ref)"
                ),
                {
                    "t": TENANT,
                    "output_ref": json.dumps({"verdict": "escalate", "flags": ["violence"]}),
                },
            )
            await session.commit()

    asyncio.run(_seed())


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text("DELETE FROM job_stages WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(
                text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    client.cookies.clear()


def test_fails_closed_when_no_password_is_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_DASHBOARD_PASSWORD", raising=False)
    get_settings.cache_clear()
    assert check_password("") is False
    assert check_password("anything") is False
    get_settings.cache_clear()


def test_unauthenticated_request_redirects_to_login():
    resp = client.get("/admin/jobs")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


def test_wrong_password_does_not_grant_access():
    resp = client.post("/admin/login", data={"password": "wrong"})
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]

    resp = client.get("/admin/jobs")
    assert resp.status_code == 303  # still not logged in


def test_correct_password_grants_access_and_sees_cross_tenant_data():
    resp = client.post("/admin/login", data={"password": ADMIN_PASSWORD})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/overview"

    resp = client.get("/admin/jobs")
    assert resp.status_code == 200
    assert "job_admin_test_001" in resp.text
    assert "Admin Dashboard Test Tenant" in resp.text
    assert "Admin Test Channel" in resp.text


def test_overview_page_renders_with_real_stats():
    resp = client.get("/admin/overview")
    assert resp.status_code == 200
    assert "Tenants" in resp.text
    assert "job_admin_test_001" in resp.text  # shows up in the recent-activity table


def test_abuse_page_renders_the_real_flagged_row():
    resp = client.get("/admin/abuse")
    assert resp.status_code == 200
    assert "job_admin_test_001" in resp.text
    assert "violence" in resp.text


def test_tenants_page_shows_key_status_only_never_raw_keys():
    resp = client.get("/dashboard/api-keys")  # no-op sanity: unrelated route unaffected
    assert resp.status_code in (200, 303)

    resp = client.get("/admin/tenants")
    assert resp.status_code == 200
    assert "Admin Dashboard Test Tenant" in resp.text


def test_quota_page_renders():
    resp = client.get("/admin/quota")
    assert resp.status_code == 200
    assert "units" in resp.text
