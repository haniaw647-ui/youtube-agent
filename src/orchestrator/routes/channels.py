import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from src.orchestrator.db import tenant_session
from src.orchestrator.supabase_auth import get_current_tenant_id

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelCreate(BaseModel):
    name: str
    niche: str | None = None
    audience: str | None = None
    language: str = "en"
    video_length_target_seconds: int | None = None
    style: str | None = None
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


@router.post("", response_model=ChannelOut)
async def create_channel(
    body: ChannelCreate, tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    async with tenant_session(tenant_id) as session:
        row = (
            (
                await session.execute(
                    text(
                        "INSERT INTO channels "
                        "(tenant_id, name, niche, audience, language, "
                        " video_length_target_seconds, style, posting_frequency) "
                        "VALUES (:tenant_id, :name, :niche, :audience, :language, "
                        " :video_length_target_seconds, :style, :posting_frequency) "
                        "RETURNING id, name, niche, audience, language, "
                        " video_length_target_seconds, style, posting_frequency"
                    ),
                    {"tenant_id": tenant_id, **body.model_dump()},
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
                        "video_length_target_seconds, style, posting_frequency "
                        "FROM channels ORDER BY created_at"
                    )
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
