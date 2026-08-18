import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from src.dashboard_tenant.auth import require_tenant
from src.orchestrator.db import tenant_session
from src.orchestrator.security import encrypt
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
    channel_id: uuid.UUID, tenant_id: uuid.UUID = Depends(require_tenant)
) -> RedirectResponse:
    # This is only ever reached via a browser link click from the dashboard
    # (channels.html's "Connect YouTube" button) — never a programmatic API
    # call, since redirecting to Google's consent screen is inherently a
    # browser-navigation flow. A browser link click can't carry a bearer
    # token, so this needs the dashboard's cookie session, not the raw API's
    # Authorization-header auth (get_current_tenant_id) it was built with —
    # which meant this button could never actually work for a real user.
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
    except YouTubeOAuthError as e:
        # The generic "oauth_failed" this used to send back left every real
        # failure indistinguishable from the browser — surfacing the actual
        # provider/validation message (also truncated, since Google's raw
        # error bodies can be long) makes this diagnosable without needing
        # server log access for every report.
        return RedirectResponse(
            f"/dashboard/channels?error={quote(str(e)[:200])}", status_code=303
        )

    return RedirectResponse("/dashboard/channels?connected=1", status_code=303)
