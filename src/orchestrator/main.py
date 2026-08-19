from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from src.dashboard_admin.auth import NotAdminAuthenticated
from src.dashboard_admin.router import router as admin_router
from src.dashboard_tenant.auth import NotAuthenticated
from src.dashboard_tenant.router import router as dashboard_router
from src.orchestrator.config import get_settings
from src.orchestrator.routes import auth, channels, jobs, tenant_keys, youtube

app = FastAPI(title="YouTube Automation Platform")

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
