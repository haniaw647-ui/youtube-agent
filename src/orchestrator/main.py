from collections.abc import MutableMapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.dashboard_admin.auth import NotAdminAuthenticated
from src.dashboard_admin.router import router as admin_router
from src.dashboard_tenant.auth import NotAuthenticated
from src.dashboard_tenant.router import router as dashboard_router
from src.orchestrator.config import get_settings
from src.orchestrator.routes import auth, channels, jobs, tenant_keys, youtube

app = FastAPI(title="YouTube Automation Platform")


class HSTSMiddleware:
    """Every request that reaches this app has already gone through
    Railway's edge over HTTPS — Railway terminates TLS in front of it, the
    app itself never sees plaintext HTTP traffic in production — so it's
    safe to stamp this unconditionally rather than branch on
    X-Forwarded-Proto. Confirmed live via Qualys SSL Labs: certificate,
    protocol support (TLS 1.3, no legacy versions), cipher strength, and
    key exchange were already an A grade with nothing left to tune (that
    layer is Railway's edge, not something this app configures) — HSTS
    was the one specific, documented gap between A and A+."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_hsts(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append(
                    (
                        b"strict-transport-security",
                        b"max-age=63072000; includeSubDomains; preload",
                    )
                )
            await send(message)

        await self.app(scope, receive, send_with_hsts)


app.add_middleware(HSTSMiddleware)

app.include_router(auth.router)
app.include_router(channels.router)
app.include_router(tenant_keys.router)
app.include_router(jobs.router)
app.include_router(youtube.router)
app.include_router(dashboard_router)
app.include_router(admin_router)


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse("/dashboard/login", status_code=303)


@app.exception_handler(NotAdminAuthenticated)
async def not_admin_authenticated_handler(
    request: Request, exc: NotAdminAuthenticated
) -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "environment": settings.environment}
