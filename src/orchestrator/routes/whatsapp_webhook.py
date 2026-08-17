from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from src.orchestrator.config import get_settings
from src.orchestrator.db import service_session
from src.orchestrator.whatsapp_webhook import verify_signature

router = APIRouter(tags=["whatsapp"])


@router.get("/webhooks/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> PlainTextResponse:
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_webhook_verify_token:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhooks/whatsapp")
async def receive_webhook(request: Request) -> dict:
    settings = get_settings()
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(body, signature, settings.whatsapp_app_secret):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for status_update in change.get("value", {}).get("statuses", []):
                message_id = status_update.get("id")
                delivery_status = status_update.get("status")
                if not (message_id and delivery_status):
                    continue
                async with service_session() as session:
                    await session.execute(
                        text(
                            "UPDATE notifications_sent SET status = :status "
                            "WHERE provider_message_id = :message_id"
                        ),
                        {"status": delivery_status, "message_id": message_id},
                    )
                    await session.commit()

    return {"status": "ok"}
