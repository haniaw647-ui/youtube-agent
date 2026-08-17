import hashlib
import hmac
import time

from fastapi import Request

from src.orchestrator.config import get_settings

SESSION_COOKIE = "admin_session"
SESSION_LIFETIME_SECONDS = 24 * 60 * 60


class NotAdminAuthenticated(Exception):
    pass


def _sign(expiry: int) -> str:
    secret = get_settings().secret_key.encode()
    digest = hmac.new(secret, f"admin:{expiry}".encode(), hashlib.sha256).hexdigest()
    return f"{expiry}:{digest}"


def check_password(password: str) -> bool:
    expected = get_settings().admin_dashboard_password
    if not expected:
        # Deliberately fail closed — an unset password must never mean "no
        # password required," which would leave every tenant's data open.
        return False
    return hmac.compare_digest(password, expected)


def make_session_cookie() -> str:
    expiry = int(time.time()) + SESSION_LIFETIME_SECONDS
    return _sign(expiry)


def _verify_session_cookie(value: str) -> bool:
    try:
        expiry_str, digest = value.split(":", 1)
        expiry = int(expiry_str)
    except ValueError:
        return False
    if expiry < int(time.time()):
        return False
    expected = _sign(expiry)
    return hmac.compare_digest(f"{expiry}:{digest}", expected)


async def require_admin(request: Request) -> None:
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie or not _verify_session_cookie(cookie):
        raise NotAdminAuthenticated
