"""Real, unmocked — proves the in-dashboard notifications feature that
replaced WhatsApp: notify_job_success/notify_job_failure write real rows to
notifications_sent (against production Postgres), notify_job_failure still
marks the job 'failed' (the one piece of real business logic the old
WhatsApp-specific version of this function did before it ever touched
WhatsApp — losing it silently would mean failed jobs stay 'running'
forever), and the dashboard's /dashboard/notifications page actually
renders those rows.
"""

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.tenants import ensure_tenant
from src.workers.notifications import notify_job_failure, notify_job_success

TENANT = uuid.uuid4()

client = TestClient(app)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Notifications Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(
                text(
                    "DELETE FROM notifications_sent WHERE job_id IN "
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


async def _seed_job(job_id: str, title: str) -> str:
    async with service_session() as session:
        channel_id = (
            await session.execute(
                text(
                    "INSERT INTO channels (tenant_id, name, approval_gates) "
                    "VALUES (:t, 'Notifications Test Channel', '{}') RETURNING id"
                ),
                {"t": TENANT},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO jobs "
                "(id, tenant_id, channel_id, current_stage, overall_status, title) "
                "VALUES (:id, :t, :cid, 'youtube_upload', 'running', :title)"
            ),
            {"id": job_id, "t": TENANT, "cid": channel_id, "title": title},
        )
        await session.commit()
    return str(channel_id)


async def _notification_row(job_id: str) -> dict | None:
    async with service_session() as session:
        row = (
            await session.execute(
                text("SELECT * FROM notifications_sent WHERE job_id = :jid"), {"jid": job_id}
            )
        ).mappings().first()
        return dict(row) if row else None


def test_notify_job_success_writes_an_in_app_notification() -> None:
    job_id = f"job_notif_success_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, "My Great Video"))

    asyncio.run(notify_job_success(job_id))

    row = asyncio.run(_notification_row(job_id))
    assert row is not None
    assert row["notify_channel"] == "in_app"
    assert row["message_type"] == "job_completed"
    assert "My Great Video" in row["detail"]


def test_notify_job_failure_marks_job_failed_and_writes_notification() -> None:
    job_id = f"job_notif_failure_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, "My Broken Video"))

    asyncio.run(notify_job_failure(job_id, str(TENANT), "video_assembly", "ffmpeg exploded"))

    async def _check() -> None:
        async with service_session() as session:
            status = (
                await session.execute(
                    text("SELECT overall_status FROM jobs WHERE id = :id"), {"id": job_id}
                )
            ).scalar_one()
            assert status == "failed"

    asyncio.run(_check())

    row = asyncio.run(_notification_row(job_id))
    assert row is not None
    assert row["notify_channel"] == "in_app"
    assert row["message_type"] == "job_failed"
    assert "My Broken Video" in row["detail"]
    assert "video_assembly" in row["detail"]


def test_notifications_page_renders_real_rows() -> None:
    job_id = f"job_notif_page_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_job(job_id, "Page Render Test Video"))
    asyncio.run(notify_job_success(job_id))

    resp = client.get("/dashboard/notifications")
    assert resp.status_code == 200
    assert "Page Render Test Video" in resp.text
