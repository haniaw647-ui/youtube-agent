from fastapi import FastAPI

from src.orchestrator.config import get_settings

app = FastAPI(title="YouTube Automation Platform")


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "environment": settings.environment}
