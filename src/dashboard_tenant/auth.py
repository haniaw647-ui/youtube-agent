import uuid

from fastapi import Request

from src.orchestrator.supabase_auth import SupabaseAuthError, get_user_from_token

SESSION_COOKIE = "session_token"


class NotAuthenticated(Exception):
    pass


async def require_tenant(request: Request) -> uuid.UUID:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise NotAuthenticated
    try:
        user = await get_user_from_token(token)
    except SupabaseAuthError as e:
        raise NotAuthenticated from e
    return uuid.UUID(user["id"])
