import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from src.orchestrator.db import tenant_session
from src.orchestrator.security import decrypt, encrypt, mask
from src.orchestrator.supabase_auth import get_current_tenant_id

router = APIRouter(prefix="/tenant/api-keys", tags=["tenant-api-keys"])

# Providers a tenant can BYO — matches API_REQUIREMENTS.md §2.
SUPPORTED_PROVIDERS = {
    "anthropic",
    "tavily",
    "serper",
    "elevenlabs",
    "openai",
    "azure_speech",
    "pexels",
    "pixabay",
    "replicate",
    "runway",
    "canva",
}


class ApiKeyCreate(BaseModel):
    provider: str
    api_key: str


class ApiKeyOut(BaseModel):
    provider: str
    masked_key: str
    status: str


@router.put("", response_model=ApiKeyOut)
async def upsert_api_key(
    body: ApiKeyCreate, tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> dict[str, Any]:
    provider = body.provider.lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    encrypted = encrypt(body.api_key)
    async with tenant_session(tenant_id) as session:
        await session.execute(
            text(
                "INSERT INTO tenant_api_keys (tenant_id, provider, encrypted_key, status) "
                "VALUES (:tenant_id, :provider, :encrypted_key, 'unvalidated') "
                "ON CONFLICT (tenant_id, provider) DO UPDATE "
                "SET encrypted_key = EXCLUDED.encrypted_key, status = 'unvalidated'"
            ),
            {"tenant_id": tenant_id, "provider": provider, "encrypted_key": encrypted},
        )
    return {"provider": provider, "masked_key": mask(body.api_key), "status": "unvalidated"}


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> list[dict[str, Any]]:
    async with tenant_session(tenant_id) as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT provider, status, encrypted_key "
                        "FROM tenant_api_keys ORDER BY provider"
                    )
                )
            )
            .mappings()
            .all()
        )

    return [
        {
            "provider": r["provider"],
            "status": r["status"],
            "masked_key": mask(decrypt(r["encrypted_key"])),
        }
        for r in rows
    ]
