"""Real DB, mocked only at the Celery dispatch boundary (matching
test_dashboard_routes.py's established pattern) — proves rejecting an
approval gate with notes turns into a revision (re-running the stage that
produced what got rejected) instead of just failing the job, for the gates
that support it, and that gates with no revision path (youtube_upload) still
just fail even when notes are supplied."""

import asyncio
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import service_session
from src.orchestrator.main import app
from src.orchestrator.tenants import ensure_tenant

TENANT = uuid.uuid4()
CHANNEL_ID = uuid.uuid4()

client = TestClient(app, follow_redirects=False)


def setup_module(_module: object) -> None:
    asyncio.run(ensure_tenant(TENANT, "Rejection Feedback Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Rejection Ch')"),
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


async def _seed_pending_approval(job_id: str, stage: str) -> None:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :c, :stage, 'awaiting_approval')"
            ),
            {"id": job_id, "t": TENANT, "c": CHANNEL_ID, "stage": stage},
        )
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status) "
                "VALUES (:id, :t, :stage, 'awaiting_approval')"
            ),
            {"id": job_id, "t": TENANT, "stage": stage},
        )
        await session.execute(
            text("INSERT INTO approvals (job_id, tenant_id, stage) VALUES (:id, :t, :stage)"),
            {"id": job_id, "t": TENANT, "stage": stage},
        )
        await session.commit()


async def _job_status(job_id: str) -> str:
    async with service_session() as session:
        return (
            await session.execute(
                text("SELECT overall_status FROM jobs WHERE id = :id"), {"id": job_id}
            )
        ).scalar_one()


async def _approval_notes(job_id: str, stage: str) -> str | None:
    async with service_session() as session:
        return (
            await session.execute(
                text(
                    "SELECT notes FROM approvals WHERE job_id = :id AND stage = :stage "
                    "ORDER BY requested_at DESC LIMIT 1"
                ),
                {"id": job_id, "stage": stage},
            )
        ).scalar_one()


def test_rejecting_script_qa_with_notes_reruns_script_writing_not_fails() -> None:
    job_id = f"job_reject_script_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_pending_approval(job_id, "script_qa"))

    with patch("src.workers.stage_runner.enqueue_stage") as mock_enqueue:
        resp = client.post(
            f"/dashboard/approvals/{job_id}/script_qa",
            data={"decision": "rejected", "notes": "Make the opening hook punchier."},
        )

    assert resp.status_code == 303
    mock_enqueue.assert_called_once_with(job_id, str(TENANT), "script_writing")
    assert asyncio.run(_job_status(job_id)) == "awaiting_approval"  # not marked failed
    assert asyncio.run(_approval_notes(job_id, "script_qa")) == "Make the opening hook punchier."


def test_rejecting_topic_scoring_with_notes_reruns_topic_generation() -> None:
    job_id = f"job_reject_topic_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_pending_approval(job_id, "topic_scoring"))

    with patch("src.workers.stage_runner.enqueue_stage") as mock_enqueue:
        resp = client.post(
            f"/dashboard/approvals/{job_id}/topic_scoring",
            data={"decision": "rejected", "notes": "Avoid anything about cooking."},
        )

    assert resp.status_code == 303
    mock_enqueue.assert_called_once_with(job_id, str(TENANT), "topic_generation")
    assert asyncio.run(_job_status(job_id)) == "awaiting_approval"


def test_rejecting_youtube_upload_with_notes_still_fails_no_revision_path() -> None:
    job_id = f"job_reject_upload_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_pending_approval(job_id, "youtube_upload"))

    with patch("src.workers.stage_runner.enqueue_stage") as mock_enqueue:
        resp = client.post(
            f"/dashboard/approvals/{job_id}/youtube_upload",
            data={"decision": "rejected", "notes": "The thumbnail looks wrong."},
        )

    assert resp.status_code == 303
    mock_enqueue.assert_not_called()
    assert asyncio.run(_job_status(job_id)) == "failed"
    assert asyncio.run(_approval_notes(job_id, "youtube_upload")) == "The thumbnail looks wrong."


def test_rejecting_script_qa_without_notes_still_fails() -> None:
    job_id = f"job_reject_no_notes_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_pending_approval(job_id, "script_qa"))

    with patch("src.workers.stage_runner.enqueue_stage") as mock_enqueue:
        resp = client.post(
            f"/dashboard/approvals/{job_id}/script_qa", data={"decision": "rejected"}
        )

    assert resp.status_code == 303
    mock_enqueue.assert_not_called()
    assert asyncio.run(_job_status(job_id)) == "failed"
