import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from src.models.pipeline import PIPELINE_STAGES
from src.orchestrator.db import tenant_session
from src.orchestrator.supabase_auth import get_current_tenant_id
from src.workers.stage_runner import enqueue_stage

router = APIRouter(tags=["jobs"])


class JobCreate(BaseModel):
    channel_id: uuid.UUID


class JobOut(BaseModel):
    id: str
    channel_id: uuid.UUID
    current_stage: str
    overall_status: str
    created_at: datetime


class JobStageOut(BaseModel):
    stage: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None


class ApproveRequest(BaseModel):
    decision: str = "approved"
    notes: str | None = None


@router.post("/channels/{channel_id}/jobs", response_model=JobOut)
async def create_job(
    channel_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    async with tenant_session(tenant_id) as session:
        channel = (
            (
                await session.execute(
                    text("SELECT id FROM channels WHERE id = :id"), {"id": channel_id}
                )
            )
            .mappings()
            .first()
        )
        if channel is None:
            raise HTTPException(status_code=404, detail="Channel not found")

        seq = (await session.execute(text("SELECT nextval('job_id_seq')"))).scalar_one()
        job_id = f"job_{datetime.now().year}_{seq:05d}"

        first_stage = PIPELINE_STAGES[0]
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO jobs "
                        "(id, tenant_id, channel_id, current_stage, overall_status) "
                        "VALUES (:id, :tenant_id, :channel_id, :stage, 'running') "
                        "RETURNING id, channel_id, current_stage, overall_status, created_at"
                    ),
                    {
                        "id": job_id,
                        "tenant_id": tenant_id,
                        "channel_id": channel_id,
                        "stage": first_stage,
                    },
                )
            )
            .mappings()
            .one()
        )

    enqueue_stage(job_id, str(tenant_id), first_stage)
    return dict(row)


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(tenant_id: uuid.UUID = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    async with tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, channel_id, current_stage, overall_status, created_at "
                        "FROM jobs ORDER BY created_at DESC"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str, tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    async with tenant_session(tenant_id) as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id, channel_id, current_stage, overall_status, created_at "
                        "FROM jobs WHERE id = :id"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return dict(row)


@router.get("/jobs/{job_id}/stages", response_model=list[JobStageOut])
async def get_job_stages(
    job_id: str, tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> list[dict[str, Any]]:
    async with tenant_session(tenant_id) as session:
        job = (
            (await session.execute(text("SELECT id FROM jobs WHERE id = :id"), {"id": job_id}))
            .mappings()
            .first()
        )
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT stage, status, started_at, finished_at FROM job_stages "
                        "WHERE job_id = :id ORDER BY started_at NULLS LAST"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@router.post("/jobs/{job_id}/approve/{stage}")
async def approve_stage(
    job_id: str,
    stage: str,
    body: ApproveRequest,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> dict[str, str]:
    async with tenant_session(tenant_id) as session:
        pending = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM job_stages WHERE job_id = :job_id AND stage = :stage "
                        "AND status = 'awaiting_approval'"
                    ),
                    {"job_id": job_id, "stage": stage},
                )
            )
            .mappings()
            .first()
        )
        if pending is None:
            raise HTTPException(status_code=404, detail="No pending approval for this stage")

        await session.execute(
            text(
                "UPDATE approvals SET resolved_at = now(), resolved_by = 'tenant', "
                "decision = :decision, notes = :notes "
                "WHERE job_id = :job_id AND stage = :stage AND resolved_at IS NULL"
            ),
            {"decision": body.decision, "notes": body.notes, "job_id": job_id, "stage": stage},
        )
        await session.execute(
            text("UPDATE jobs SET overall_status = 'running' WHERE id = :job_id"),
            {"job_id": job_id},
        )
        await session.execute(
            text("DELETE FROM job_stages WHERE job_id = :job_id AND stage = :stage"),
            {"job_id": job_id, "stage": stage},
        )

    if body.decision == "approved":
        enqueue_stage(job_id, str(tenant_id), stage)
    return {"status": "resumed" if body.decision == "approved" else "rejected"}
