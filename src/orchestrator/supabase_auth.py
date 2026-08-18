import uuid

import httpx
from fastapi import Header, HTTPException

from src.orchestrator.config import get_settings

settings = get_settings()


class SupabaseAuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


async def signup(email: str, password: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.supabase_url}/auth/v1/signup",
            headers={"apikey": settings.supabase_anon_key},
            json={"email": email, "password": password},
        )
    if resp.status_code >= 400:
        raise SupabaseAuthError(resp.status_code, resp.text)
    return resp.json()


async def login(email: str, password: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": settings.supabase_anon_key},
            json={"email": email, "password": password},
        )
    if resp.status_code >= 400:
        raise SupabaseAuthError(resp.status_code, resp.text)
    return resp.json()


async def request_password_reset(email: str, redirect_to: str) -> None:
    """Supabase's own /auth/v1/recover deliberately returns 200 for both
    existing and non-existent emails (prevents account enumeration by a
    login form), and this never raises on a non-2xx either — callers should
    always show the same generic "check your email" message regardless of
    what actually happened here."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.supabase_url}/auth/v1/recover",
            headers={"apikey": settings.supabase_anon_key},
            params={"redirect_to": redirect_to},
            json={"email": email},
        )


async def get_user_from_token(access_token: str) -> dict:
    """Delegates token verification to Supabase Auth itself over HTTPS — the app
    never needs the project's JWT signing secret."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.supabase_url}/auth/v1/user",
            headers={
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {access_token}",
            },
        )
    if resp.status_code >= 400:
        raise SupabaseAuthError(resp.status_code, resp.text)
    return resp.json()


async def get_current_tenant_id(authorization: str = Header(...)) -> uuid.UUID:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        user = await get_user_from_token(token)
    except SupabaseAuthError as e:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from e
    return uuid.UUID(user["id"])
