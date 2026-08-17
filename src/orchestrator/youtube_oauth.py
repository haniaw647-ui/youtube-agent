import urllib.parse
import uuid

import httpx

from src.orchestrator.config import get_settings
from src.orchestrator.security import decrypt, encrypt

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
# youtube.upload covers status/thumbnails too; readonly isn't separately
# needed since upload scope already permits reading the caller's own channel.
SCOPES = "https://www.googleapis.com/auth/youtube.upload"


class YouTubeOAuthError(Exception):
    pass


def seal_state(tenant_id: uuid.UUID, channel_id: uuid.UUID) -> str:
    """The OAuth callback is a browser redirect from Google — it carries no
    bearer token, so tenant/channel identity has to travel in `state`. Fernet
    (already used for tenant_api_keys/refresh tokens) makes this tamper-proof:
    only a state string we ourselves sealed will decrypt, so nothing else can
    forge a callback that attaches a channel to the wrong tenant."""
    return encrypt(f"{tenant_id}:{channel_id}")


def unseal_state(state: str) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        tenant_str, channel_str = decrypt(state).split(":")
        return uuid.UUID(tenant_str), uuid.UUID(channel_str)
    except Exception as e:
        raise YouTubeOAuthError("Invalid or tampered OAuth state") from e


def build_authorization_url(state: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.youtube_oauth_client_id,
        "redirect_uri": settings.youtube_oauth_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_tokens(code: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.youtube_oauth_client_id,
                "client_secret": settings.youtube_oauth_client_secret,
                "redirect_uri": settings.youtube_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if resp.status_code >= 400:
        raise YouTubeOAuthError(f"Token exchange failed: {resp.text}")
    return resp.json()


async def fetch_channel_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            CHANNELS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"part": "snippet", "mine": "true"},
        )
    if resp.status_code >= 400:
        raise YouTubeOAuthError(f"Fetching channel info failed: {resp.text}")
    items = resp.json().get("items", [])
    if not items:
        raise YouTubeOAuthError("Authenticated Google account has no YouTube channel")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}
