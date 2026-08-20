import json
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text

from src.models.pipeline import PIPELINE_STAGES
from src.orchestrator.db import service_session
from src.orchestrator.timeutil import utcnow_naive
from src.workers.notifications import notify_job_awaiting_approval, notify_job_success
from src.workers.stages import (
    background_music,
    final_qa,
    metadata_generation,
    research,
    script_qa,
    script_writing,
    subtitle_burn_in,
    thumbnail_generation,
    topic_generation,
    topic_scoring,
    video_assembly,
    visual_generation,
    voice_over,
    youtube_upload,
)

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

# Real implementations for every pipeline stage.
StageFn = Callable[[str, str], Awaitable[dict]]
STAGE_IMPLEMENTATIONS: dict[str, StageFn] = {
    "topic_generation": topic_generation.run,
    "topic_scoring": topic_scoring.run,
    "research": research.run,
    "script_writing": script_writing.run,
    "script_qa": script_qa.run,
    "voice_over": voice_over.run,
    "visual_generation": visual_generation.run,
    "video_assembly": video_assembly.run,
    "subtitle_burn_in": subtitle_burn_in.run,
    "background_music": background_music.run,
    "thumbnail_generation": thumbnail_generation.run,
    "metadata_generation": metadata_generation.run,
    "final_qa": final_qa.run,
    "youtube_upload": youtube_upload.run,
}


def queue_for_stage(stage: str) -> str:
    return "heavy" if stage in HEAVY_STAGES else "light"


def _next_stage(stage: str) -> str | None:
    idx = PIPELINE_STAGES.index(stage)
    return PIPELINE_STAGES[idx + 1] if idx + 1 < len(PIPELINE_STAGES) else None


async def _insert_running_stage(job_id: str, tenant_id: uuid.UUID, stage: str) -> uuid.UUID:
    async with service_session() as session:
        stage_row_id = (
            await session.execute(
                text(
                    "INSERT INTO job_stages (job_id, tenant_id, stage, status, started_at) "
                    "VALUES (:job_id, :tenant_id, :stage, 'running', :now) RETURNING id"
                ),
                {
                    "job_id": job_id,
                    "tenant_id": tenant_id,
                    "stage": stage,
                    "now": utcnow_naive(),
                },
            )
        ).scalar_one()
        # jobs.current_stage previously only advanced when a stage *finished*
        # successfully, so a job stuck retrying a stage (or dead after
        # exhausting retries) showed the last stage that actually completed
        # instead of the one it's stuck on — confirmed live, genuinely
        # confusing on the dashboard. Stamping it here, the moment a stage
        # attempt actually starts, keeps it reflecting reality even mid-retry.
        # overall_status='running' is the companion fix: resuming a job from
        # 'awaiting_approval' (or a revision loop) never reset it, so a
        # resumed job kept showing 'awaiting_approval' on the dashboard even
        # while a stage was actively running/retrying — every real stage
        # attempt passes through here, so it's the correct single place to
        # keep both fields honest.
        await session.execute(
            text(
                "UPDATE jobs SET current_stage = :stage, overall_status = 'running', "
                "updated_at = :now WHERE id = :job_id"
            ),
            {"stage": stage, "now": utcnow_naive(), "job_id": job_id},
        )
        await session.commit()
    return stage_row_id


async def execute_stage(job_id: str, tenant_id: str, stage: str) -> None:
    """A stage can legitimately run more than once for the same job (script_qa's
    revision loop re-runs script_writing/script_qa), so every execution gets its
    own job_stages row, targeted by that row's own id — never by (job_id, stage),
    which would match every prior attempt too."""
    tid = uuid.UUID(tenant_id)
    stage_row_id = await _insert_running_stage(job_id, tid, stage)

    try:
        if stage in STAGE_IMPLEMENTATIONS:
            output_ref = await STAGE_IMPLEMENTATIONS[stage](job_id, tenant_id)
        else:
            output_ref = {"stub": True, "stage": stage}
    except Exception as e:
        async with service_session() as session:
            await session.execute(
                text(
                    "UPDATE job_stages SET status = 'failed', finished_at = :now, error = :error "
                    "WHERE id = :id"
                ),
                {"id": stage_row_id, "now": utcnow_naive(), "error": str(e)[:2000]},
            )
            await session.commit()
        raise

    async with service_session() as session:
        # current_stage is already stamped by _insert_running_stage when this
        # attempt started; no need to set it again on success.
        await session.execute(
            text(
                "UPDATE job_stages SET status = 'done', finished_at = :now, "
                "output_ref = :output_ref WHERE id = :id"
            ),
            {"id": stage_row_id, "now": utcnow_naive(), "output_ref": json.dumps(output_ref)},
        )
        await session.commit()

    if stage == "script_qa":
        await _advance_script_qa(job_id, tenant_id, output_ref)
        return
    if stage == "final_qa":
        await _advance_final_qa(job_id, tenant_id, output_ref)
        return

    await _advance(job_id, tenant_id, stage)


async def _advance_script_qa(job_id: str, tenant_id: str, result: dict) -> None:
    verdict = result.get("verdict")
    if verdict == "pass":
        await _try_enqueue(job_id, tenant_id, "voice_over")
        await _try_enqueue(job_id, tenant_id, "visual_generation")
    elif verdict == "revise":
        enqueue_stage(job_id, tenant_id, "script_writing")
    elif verdict == "escalate":
        await _mark_awaiting_approval(job_id, uuid.UUID(tenant_id), "script_qa")
    else:
        raise ValueError(f"Unexpected script_qa verdict: {verdict!r}")


async def _advance_final_qa(job_id: str, tenant_id: str, result: dict) -> None:
    """A failed checklist (including an unresolved license — ARCHITECTURE.md
    §11) always forces human sign-off before youtube_upload, regardless of
    the channel's own approval_gates config — this is the one gate that isn't
    tenant-overridable off. A clean pass still respects the channel's own
    gate setting via the normal _advance path, which new channels default to
    on (routes/channels.py) but a tenant can turn off for full autonomy."""
    if not result.get("passed", False):
        await _mark_awaiting_approval(job_id, uuid.UUID(tenant_id), "youtube_upload")
        return
    await _advance(job_id, tenant_id, "final_qa")


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


async def _mark_awaiting_approval(job_id: str, tenant_id: uuid.UUID, stage: str) -> None:
    """Inserts a fresh job_stages row in 'awaiting_approval' status — this is
    what the approve endpoint (routes/jobs.py) looks for. It's a distinct row
    from any prior 'done'/'failed' attempts at this stage (e.g. script_qa's
    revision history), never a mutation of them, so approval never clobbers
    that history."""
    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO job_stages (job_id, tenant_id, stage, status) "
                "VALUES (:job_id, :tenant_id, :stage, 'awaiting_approval')"
            ),
            {"job_id": job_id, "tenant_id": tenant_id, "stage": stage},
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
            {"job_id": job_id, "tenant_id": tenant_id, "stage": stage},
        )
        await session.commit()
    await notify_job_awaiting_approval(job_id, str(tenant_id), stage)


async def resume_after_approval(job_id: str, tenant_id: str, stage: str) -> None:
    """Called by the approve endpoint once a human has resolved an
    awaiting_approval gate. Two different situations share this path:
    a stage gated *before* it ever ran (e.g. a channel-config gate on
    topic_scoring) just needs to be enqueued for the first time; script_qa
    escalating *after* running out of revision attempts means a human is
    overriding QA and accepting the script as-is, which resumes at the
    parallel voice/visual stages rather than re-running QA."""
    if stage == "script_qa":
        await _try_enqueue(job_id, tenant_id, "voice_over")
        await _try_enqueue(job_id, tenant_id, "visual_generation")
        return
    enqueue_stage(job_id, tenant_id, stage)


# Gates where rejecting with notes can be turned into a real revision instead
# of just killing the job — the value is the stage to re-run, which reads the
# tenant's notes back out of the approvals table itself (script_writing.py /
# topic_generation.py) rather than needing them passed through here.
# youtube_upload -> visual_generation: re-fetches different stock photos
# (visual_generation.py takes a different result index once it sees this
# rejection) and re-renders from there — video_assembly is a PARALLEL_STAGE
# sibling wait on voice_over, which is already 'done' from the first run,
# so the existing _advance() machinery cascades straight through
# video_assembly -> ... -> youtube_upload with no extra wiring needed.
# Script/voice aren't in question for a "change the photos"/"too blurry"
# complaint, so they're deliberately not re-run.
REJECTION_REVISION_SOURCE_STAGE = {
    "script_qa": "script_writing",
    "topic_scoring": "topic_generation",
    "youtube_upload": "visual_generation",
}


def resume_after_rejection_with_feedback(job_id: str, tenant_id: str, stage: str) -> bool:
    """Called by the reject endpoint when the tenant left notes on a gate that
    supports turning rejection into a revision."""
    source_stage = REJECTION_REVISION_SOURCE_STAGE.get(stage)
    if source_stage is None:
        return False
    enqueue_stage(job_id, tenant_id, source_stage)
    return True


async def _mark_job_done(job_id: str) -> None:
    async with service_session() as session:
        await session.execute(
            text("UPDATE jobs SET overall_status = 'done' WHERE id = :job_id"),
            {"job_id": job_id},
        )
        await session.commit()
    await notify_job_success(job_id)


async def _sibling_done(job_id: str, sibling_stage: str) -> bool:
    async with service_session() as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT status FROM job_stages WHERE job_id = :job_id AND stage = :stage "
                        "ORDER BY started_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id, "stage": sibling_stage},
                )
            )
            .mappings()
            .first()
        )
    return bool(row and row["status"] == "done")


async def _advance(job_id: str, tenant_id: str, completed_stage: str) -> None:
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
        await _mark_awaiting_approval(job_id, uuid.UUID(tenant_id), stage)
        return
    enqueue_stage(job_id, tenant_id, stage)


def enqueue_stage(job_id: str, tenant_id: str, stage: str) -> None:
    from src.workers.tasks.heavy import run_heavy_stage
    from src.workers.tasks.light import run_light_stage

    task = run_heavy_stage if queue_for_stage(stage) == "heavy" else run_light_stage
    task.delay(job_id, tenant_id, stage)
