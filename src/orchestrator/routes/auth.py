import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr

from src.orchestrator.supabase_auth import SupabaseAuthError, login, signup
from src.orchestrator.tenants import ensure_tenant

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/signup")
async def signup_route(body: SignupRequest) -> dict:
    try:
        result = await signup(body.email, body.password)
    except SupabaseAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    # If email confirmation is off, Supabase returns a session immediately and
    # we can create the tenant row now. If confirmation is required, there's no
    # session yet — the tenant row is created lazily on first successful login.
    if "access_token" in result and "id" in result.get("user", result):
        user_id = result.get("user", result)["id"]
        await ensure_tenant(uuid.UUID(user_id), body.display_name)

    return result


@router.post("/login")
async def login_route(body: LoginRequest) -> dict:
    try:
        result = await login(body.email, body.password)
    except SupabaseAuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    user_id = result["user"]["id"]
    display_name = result["user"].get("email", "")
    await ensure_tenant(uuid.UUID(user_id), display_name)

    return result
