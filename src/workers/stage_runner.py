import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from src.models.pipeline import PIPELINE_STAGES
from src.orchestrator.db import service_session

# Matches the async/sync split in ARCHITECTURE.md §8: text/API-bound stages are
# 'light', render/upload-bound stages are 'heavy' and get their own worker pool.
HEAVY_STAGES = {
    "voice_over",
    "visual_generation",
    "video_assembly",
    "subtitle_burn_in",
    "background_music",
    "youtube_upload",
}

PARALLEL_STAGES = {"voice_over", "visual_generation"}
STAGE_AFTER_PARALLEL = "video_assembly"


def queue_for_stage(stage: str) -> str:
    return "heavy" if stage in HEAVY_STAGES else "light"


def _next_stage(stage: str) -> str | None:
    idx = PIPELINE_STAGES.index(stage)
    return PIPELINE_STAGES[idx + 1] if idx + 1 < len(PIPELINE_STAGES) else None


async def execute_stage(job_id: str, tenant_id: str, stage: str) -> None:
    """Stub implementation: writes a fake artifact and marks the stage done.
    Real provider calls replace this task-by-task from Phase 2 onward — the
    point of this phase is to prove sequencing, parallelism, retries, and
    approval-gate pause/resume work before any real API cost is on the line."""
    tid = uuid.UUID(tenant_id)
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status, started_at) "
                "VALUES (:job_id, :tenant_id, :stage, 'running', :now) "
                "ON CONFLICT DO NOTHING"
            ),
            {"job_id": job_id, "tenant_id": tid, "stage": stage, "now": datetime.now(UTC)},
        )
        await session.execute(
            text(
                "UPDATE job_stages SET status = 'done', finished_at = :now, "
                "output_ref = :output_ref "
                "WHERE job_id = :job_id AND stage = :stage"
            ),
            {
                "job_id": job_id,
                "stage": stage,
                "now": datetime.now(UTC),
                "output_ref": f'{{"stub": true, "stage": "{stage}"}}',
            },
        )
        await session.execute(
            text("UPDATE jobs SET current_stage = :stage, updated_at = :now WHERE id = :job_id"),
            {"stage": stage, "now": datetime.now(UTC), "job_id": job_id},
        )
        await session.commit()

    await _advance(job_id, tenant_id, stage)


async def _channel_approval_gates(job_id: str) -> dict:
    async with service_session() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT c.approval_gates FROM jobs j "
                        "JOIN channels c ON c.id = j.channel_id WHERE j.id = :job_id"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .one()
        )
    return row["approval_gates"] or {}


async def _mark_awaiting_approval(job_id: str, tenant_id: str, stage: str) -> None:
    tid = uuid.UUID(tenant_id)
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status) "
                "VALUES (:job_id, :tenant_id, :stage, 'awaiting_approval') "
                "ON CONFLICT DO NOTHING"
            ),
            {"job_id": job_id, "tenant_id": tid, "stage": stage},
        )
        await session.execute(
            text("UPDATE jobs SET overall_status = 'awaiting_approval' WHERE id = :job_id"),
            {"job_id": job_id},
        )
        await session.execute(
            text(
                "INSERT INTO approvals (job_id, tenant_id, stage) "
                "VALUES (:job_id, :tenant_id, :stage)"
            ),
            {"job_id": job_id, "tenant_id": tid, "stage": stage},
        )
        await session.commit()


async def _mark_job_done(job_id: str) -> None:
    async with service_session() as session:
        await session.execute(
            text("UPDATE jobs SET overall_status = 'done' WHERE id = :job_id"),
            {"job_id": job_id},
        )
        await session.commit()


async def _sibling_done(job_id: str, sibling_stage: str) -> bool:
    async with service_session() as session:
        row = (
            (
                await session.execute(
                    text("SELECT status FROM job_stages WHERE job_id = :job_id AND stage = :stage"),
                    {"job_id": job_id, "stage": sibling_stage},
                )
            )
            .mappings()
            .first()
        )
    return bool(row and row["status"] == "done")


async def _advance(job_id: str, tenant_id: str, completed_stage: str) -> None:
    if completed_stage == "script_qa":
        await _try_enqueue(job_id, tenant_id, "voice_over")
        await _try_enqueue(job_id, tenant_id, "visual_generation")
        return

    if completed_stage in PARALLEL_STAGES:
        sibling = (PARALLEL_STAGES - {completed_stage}).pop()
        if await _sibling_done(job_id, sibling):
            await _try_enqueue(job_id, tenant_id, STAGE_AFTER_PARALLEL)
        return

    upcoming = _next_stage(completed_stage)
    if upcoming is None:
        await _mark_job_done(job_id)
        return
    await _try_enqueue(job_id, tenant_id, upcoming)


async def _try_enqueue(job_id: str, tenant_id: str, stage: str) -> None:
    gates = await _channel_approval_gates(job_id)
    if gates.get(stage):
        await _mark_awaiting_approval(job_id, tenant_id, stage)
        return
    enqueue_stage(job_id, tenant_id, stage)


def enqueue_stage(job_id: str, tenant_id: str, stage: str) -> None:
    from src.workers.tasks.heavy import run_heavy_stage
    from src.workers.tasks.light import run_light_stage

    task = run_heavy_stage if queue_for_stage(stage) == "heavy" else run_light_stage
    task.delay(job_id, tenant_id, stage)
