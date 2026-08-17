import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from src.orchestrator.db import tenant_session
from src.orchestrator.security import encrypt
from src.orchestrator.supabase_auth import get_current_tenant_id
from src.orchestrator.youtube_oauth import (
    YouTubeOAuthError,
    build_authorization_url,
    exchange_code_for_tokens,
    fetch_channel_info,
    seal_state,
    unseal_state,
)

router = APIRouter(tags=["youtube"])


@router.get("/channels/{channel_id}/youtube/connect")
async def youtube_connect(
    channel_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_current_tenant_id)
) -> RedirectResponse:
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

    state = seal_state(tenant_id, channel_id)
    return RedirectResponse(build_authorization_url(state))


@router.get("/youtube/callback")
async def youtube_callback(
    code: str | None = Query(None), state: str | None = Query(None), error: str | None = Query(None)
) -> RedirectResponse:
    if error or not code or not state:
        return RedirectResponse(
            f"/dashboard/channels?error={error or 'missing_code'}", status_code=303
        )

    try:
        tenant_id, channel_id = unseal_state(state)
        tokens = await exchange_code_for_tokens(code)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            # Google omits this if the tenant already granted consent before
            # without prompt=consent forcing a fresh one — shouldn't happen
            # given build_authorization_url always sets prompt=consent, but
            # fail loudly rather than silently storing nothing to refresh with.
            raise YouTubeOAuthError("No refresh_token returned by Google")
        channel_info = await fetch_channel_info(tokens["access_token"])

        async with tenant_session(tenant_id) as session:
            await session.execute(
                text(
                    "UPDATE channels SET youtube_channel_id = :yt_channel_id, "
                    "youtube_refresh_token_encrypted = :refresh_token WHERE id = :id"
                ),
                {
                    "yt_channel_id": channel_info["id"],
                    "refresh_token": encrypt(refresh_token),
                    "id": channel_id,
                },
            )
    except YouTubeOAuthError:
        return RedirectResponse("/dashboard/channels?error=oauth_failed", status_code=303)

    return RedirectResponse("/dashboard/channels?connected=1", status_code=303)
