import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from src.orchestrator.db import tenant_session
from src.orchestrator.supabase_auth import get_current_tenant_id

router = APIRouter(prefix="/channels", tags=["channels"])

# IMPLEMENTATION_PLAN.md Phase 6: the final_qa -> youtube_upload gate defaults
# on for every new channel — tenant-overridable via ChannelCreate.approval_gates,
# but never silently off. This is distinct from final_qa's own hard gate
# (stage_runner._advance_final_qa), which fires regardless of this setting
# when the checklist fails or a license is unresolved.
DEFAULT_APPROVAL_GATES = {"youtube_upload": True}


class ChannelCreate(BaseModel):
    name: str
    niche: str | None = None
    audience: str | None = None
    language: str = "en"
    video_length_target_seconds: int | None = None
    style: str | None = None
    posting_frequency: str | None = None
    approval_gates: dict[str, bool] | None = None


class ChannelOut(BaseModel):
    id: uuid.UUID
    name: str
    niche: str | None
    audience: str | None
    language: str
    video_length_target_seconds: int | None
    style: str | None
    posting_frequency: str | None
    approval_gates: dict[str, Any]


@router.post("", response_model=ChannelOut)
async def create_channel(
    body: ChannelCreate, tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    approval_gates = (
        body.approval_gates if body.approval_gates is not None else DEFAULT_APPROVAL_GATES
    )
    fields = body.model_dump(exclude={"approval_gates"})
    async with tenant_session(tenant_id) as session:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO channels "
                        "(tenant_id, name, niche, audience, language, "
                        " video_length_target_seconds, style, posting_frequency, approval_gates) "
                        "VALUES (:tenant_id, :name, :niche, :audience, :language, "
                        " :video_length_target_seconds, :style, :posting_frequency, "
                        " :approval_gates) "
                        "RETURNING id, name, niche, audience, language, "
                        " video_length_target_seconds, style, posting_frequency, approval_gates"
                    ),
                    {
                        "tenant_id": tenant_id,
                        **fields,
                        "approval_gates": json.dumps(approval_gates),
                    },
                )
            )
            .mappings()
            .one()
        )
    return dict(row)


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> list[dict[str, Any]]:
    async with tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, name, niche, audience, language, "
                        "video_length_target_seconds, style, posting_frequency, approval_gates "
                        "FROM channels ORDER BY created_at"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
