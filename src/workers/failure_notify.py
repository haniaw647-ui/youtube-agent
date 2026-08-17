from sqlalchemy import text

from src.orchestrator.config import get_settings
from src.orchestrator.db import service_session
from src.providers.whatsapp.whatsapp_api import WhatsAppCloudAPIProvider

MESSAGE_TYPE = "job_failed"


async def notify_job_failure(job_id: str, tenant_id: str, stage: str, error: str) -> None:
    """Called only once a stage's retries are truly exhausted (see the
    `self.request.retries >= self.max_retries` check in tasks/light.py and
    tasks/heavy.py) — this is the "terminal failure" moment IMPLEMENTATION_PLAN
    Phase 8 asks for, not just any single failed attempt."""
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT channel_id, tenant_id, title FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            )
            .mappings()
            .first()
        )
        if job_row is None:
            return
        channel = (
            (
                await session.execute(
                    text("SELECT whatsapp_recipient_number FROM channels WHERE id = :id"),
                    {"id": job_row["channel_id"]},
                )
            )
            .mappings()
            .first()
        )
        await session.execute(
            text("UPDATE jobs SET overall_status = 'failed' WHERE id = :id"), {"id": job_id}
        )
        await session.commit()

    recipient = channel["whatsapp_recipient_number"] if channel else None
    if not recipient:
        return

    settings = get_settings()
    provider = WhatsAppCloudAPIProvider(
        phone_number_id=settings.whatsapp_phone_number_id,
        access_token=settings.whatsapp_access_token,
    )
    title = job_row["title"] or job_id

    try:
        result = await provider.send_template_message(
            to=recipient,
            template_name=settings.whatsapp_template_name,
            params=[title, f"Failed at {stage}", error[:200]],
        )
        message_id = (result.get("messages") or [{}])[0].get("id")
        status = "sent"
    except Exception:
        # A failed notification attempt must never mask the original job
        # failure this function was called to report — swallow and record it.
        message_id = None
        status = "send_failed"

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO notifications_sent "
                "(tenant_id, job_id, notify_channel, message_type, status, provider_message_id) "
                "VALUES (:tenant_id, :job_id, 'whatsapp', :message_type, :status, :message_id)"
            ),
            {
                "tenant_id": job_row["tenant_id"],
                "job_id": job_id,
                "message_type": MESSAGE_TYPE,
                "status": status,
                "message_id": message_id,
            },
        )
        await session.commit()
