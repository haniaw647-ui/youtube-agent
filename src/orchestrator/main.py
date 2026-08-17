from fastapi import FastAPI

from src.orchestrator.config import get_settings
from src.orchestrator.routes import auth, channels, jobs, tenant_keys

app = FastAPI(title="YouTube Automation Platform")

app.include_router(auth.router)
app.include_router(channels.router)
app.include_router(tenant_keys.router)
app.include_router(jobs.router)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "environment": settings.environment}
