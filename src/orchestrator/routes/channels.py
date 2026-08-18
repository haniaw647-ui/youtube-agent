import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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

CHANNEL_FIELDS = (
    "id, name, niche, audience, language, video_length_target_seconds, style, "
    "posting_frequency, approval_gates, whatsapp_recipient_number"
)


class ChannelCreate(BaseModel):
    name: str
    niche: str | None = None
    audience: str | None = None
    language: str = "en"
    video_length_target_seconds: int | None = None
    style: str | None = None
    posting_frequency: str | None = None
    approval_gates: dict[str, bool] | None = None
    whatsapp_recipient_number: str | None = None


class ChannelUpdate(BaseModel):
    whatsapp_recipient_number: str | None = None
    # Phase 10: "per-tenant auto-approve rules for channels configured for
    # full autonomy" + "Celery Beat triggers topic generation on each
    # channel's cadence" — both were only ever settable at creation time
    # before this. See src/workers/scheduler.py for the recognized
    # posting_frequency values.
    approval_gates: dict[str, bool] | None = None
    posting_frequency: str | None = None


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
    whatsapp_recipient_number: str | None


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
                        " video_length_target_seconds, style, posting_frequency, approval_gates, "
                        " whatsapp_recipient_number) "
                        "VALUES (:tenant_id, :name, :niche, :audience, :language, "
                        " :video_length_target_seconds, :style, :posting_frequency, "
                        " :approval_gates, :whatsapp_recipient_number) "
                        f"RETURNING {CHANNEL_FIELDS}"
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
                    text(f"SELECT {CHANNEL_FIELDS} FROM channels ORDER BY created_at")
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> dict[str, Any]:
    # Only touch fields the caller actually included — a PATCH that sets just
    # posting_frequency must never silently null out approval_gates, etc.
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "approval_gates" in updates:
        updates["approval_gates"] = json.dumps(updates["approval_gates"])

    set_clause = ", ".join(f"{field} = :{field}" for field in updates)
    async with tenant_session(tenant_id) as session:
        row = (
            (
                await session.execute(
                    text(
                        f"UPDATE channels SET {set_clause} "
                        f"WHERE id = :id RETURNING {CHANNEL_FIELDS}"
                    ),
                    {"id": channel_id, **updates},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return dict(row)
