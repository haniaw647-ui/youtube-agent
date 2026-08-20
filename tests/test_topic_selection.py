"""Real DB, mocked only at the Celery dispatch boundary — proves a tenant can
pick a specific topic candidate at the topic_scoring approval gate instead of
only ever approving/rejecting the whole AI-scored batch. Selecting a
candidate must mark it 'selected', every sibling 'rejected', set
jobs.topic_id, and skip straight to the stage after topic_scoring (research)
rather than running topic_scoring's own highest-score auto-pick."""

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
    asyncio.run(ensure_tenant(TENANT, "Topic Selection Test Tenant"))
    app.dependency_overrides[require_tenant] = lambda: TENANT
    asyncio.run(_insert_channel())


async def _insert_channel() -> None:
    async with service_session() as session:
        await session.execute(
            text("INSERT INTO channels (id, tenant_id, name) VALUES (:id, :t, 'Topic Sel Ch')"),
            {"id": CHANNEL_ID, "t": TENANT},
        )
        await session.commit()


def teardown_module(_module: object) -> None:
    async def _cleanup() -> None:
        async with service_session() as session:
            await session.execute(text("DELETE FROM approvals WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(
                text("DELETE FROM job_stages WHERE tenant_id = :t"), {"t": TENANT}
            )
            # jobs.topic_id <-> topics.job_id is a mutual FK — null the
            # jobs side first or deleting topics violates the constraint.
            await session.execute(
                text("UPDATE jobs SET topic_id = NULL WHERE tenant_id = :t"), {"t": TENANT}
            )
            await session.execute(text("DELETE FROM topics WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM jobs WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM channels WHERE tenant_id = :t"), {"t": TENANT})
            await session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": TENANT})
            await session.commit()

    asyncio.run(_cleanup())
    app.dependency_overrides.clear()


async def _seed_pending_topic_scoring(job_id: str) -> dict[str, str]:
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO jobs (id, tenant_id, channel_id, current_stage, overall_status) "
                "VALUES (:id, :t, :c, 'topic_scoring', 'awaiting_approval')"
            ),
            {"id": job_id, "t": TENANT, "c": CHANNEL_ID},
        )
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status) "
                "VALUES (:id, :t, 'topic_scoring', 'awaiting_approval')"
            ),
            {"id": job_id, "t": TENANT},
        )
        await session.execute(
            text("INSERT INTO approvals (job_id, tenant_id, stage) VALUES (:id, :t, :stage)"),
            {"id": job_id, "t": TENANT, "stage": "topic_scoring"},
        )
        # The high scorer is NOT the one the tenant will pick — proves the
        # selection genuinely overrides the auto-pick-highest-score logic,
        # not just happening to agree with it.
        high_score_id = (
            await session.execute(
                text(
                    "INSERT INTO topics (tenant_id, channel_id, job_id, title, score, status) "
                    "VALUES (:t, :c, :jid, 'Highest scored but not wanted', 95, 'candidate') "
                    "RETURNING id"
                ),
                {"t": TENANT, "c": CHANNEL_ID, "jid": job_id},
            )
        ).scalar_one()
        chosen_id = (
            await session.execute(
                text(
                    "INSERT INTO topics (tenant_id, channel_id, job_id, title, score, status) "
                    "VALUES (:t, :c, :jid, 'Lower scored but actually wanted', 60, 'candidate') "
                    "RETURNING id"
                ),
                {"t": TENANT, "c": CHANNEL_ID, "jid": job_id},
            )
        ).scalar_one()
        await session.commit()
    return {"high_score_id": str(high_score_id), "chosen_id": str(chosen_id)}


async def _topic_statuses(job_id: str) -> dict[str, str]:
    async with service_session() as session:
        rows = (
            await session.execute(
                text("SELECT id, status FROM topics WHERE job_id = :jid"), {"jid": job_id}
            )
        ).mappings().all()
        return {str(r["id"]): r["status"] for r in rows}


async def _job_topic_id_and_status(job_id: str) -> dict:
    async with service_session() as session:
        return dict(
            (
                await session.execute(
                    text("SELECT topic_id, overall_status FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            ).mappings().one()
        )


def test_selecting_a_lower_scored_candidate_overrides_the_auto_pick() -> None:
    job_id = f"job_topicsel_{uuid.uuid4().hex[:8]}"
    ids = asyncio.run(_seed_pending_topic_scoring(job_id))

    with patch("src.dashboard_tenant.router.enqueue_stage") as mock_enqueue:
        resp = client.post(
            f"/dashboard/approvals/{job_id}/topic_scoring",
            data={"decision": "approved", "selected_topic_id": ids["chosen_id"]},
        )

    assert resp.status_code == 303, resp.text
    mock_enqueue.assert_called_once_with(job_id, str(TENANT), "research")

    statuses = asyncio.run(_topic_statuses(job_id))
    assert statuses[ids["chosen_id"]] == "selected"
    assert statuses[ids["high_score_id"]] == "rejected"

    job_row = asyncio.run(_job_topic_id_and_status(job_id))
    assert str(job_row["topic_id"]) == ids["chosen_id"]


def test_approving_without_a_selection_falls_back_to_auto_pick() -> None:
    job_id = f"job_topicsel_noselect_{uuid.uuid4().hex[:8]}"
    asyncio.run(_seed_pending_topic_scoring(job_id))

    with patch("src.workers.stage_runner.enqueue_stage") as mock_enqueue:
        resp = client.post(
            f"/dashboard/approvals/{job_id}/topic_scoring", data={"decision": "approved"}
        )

    assert resp.status_code == 303, resp.text
    # No override — resume_after_approval's normal path enqueues topic_scoring
    # itself so its own highest-score auto-pick still runs.
    mock_enqueue.assert_called_once_with(job_id, str(TENANT), "topic_scoring")

    statuses = asyncio.run(_topic_statuses(job_id))
    assert set(statuses.values()) == {"candidate"}  # untouched — auto-pick hasn't run yet
