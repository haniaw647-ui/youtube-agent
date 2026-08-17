from sqlalchemy import text

from src.orchestrator.config import get_settings
from src.orchestrator.db import service_session
from src.providers.whatsapp.whatsapp_api import WhatsAppCloudAPIProvider

MESSAGE_TYPE = "job_completed"


async def run(job_id: str, tenant_id: str) -> dict:
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT channel_id, tenant_id, title FROM jobs WHERE id = :id"),
                    {"id": job_id},
                )
            )
            .mappings()
            .one()
        )
        channel = (
            (
                await session.execute(
                    text("SELECT whatsapp_recipient_number FROM channels WHERE id = :id"),
                    {"id": job_row["channel_id"]},
                )
            )
            .mappings()
            .one()
        )
        video = (
            (
                await session.execute(
                    text(
                        "SELECT url FROM youtube_videos WHERE job_id = :job_id "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"job_id": job_id},
                )
            )
            .mappings()
            .first()
        )

    recipient = channel["whatsapp_recipient_number"]
    if not recipient:
        # Notification setup isn't mandatory for the pipeline to complete —
        # a tenant who hasn't configured a number yet shouldn't have their
        # otherwise-successful job marked as failed over it.
        return {"skipped": True, "reason": "no whatsapp_recipient_number configured"}

    settings = get_settings()
    provider = WhatsAppCloudAPIProvider(
        phone_number_id=settings.whatsapp_phone_number_id,
        access_token=settings.whatsapp_access_token,
    )
    title = job_row["title"] or job_id
    video_url = video["url"] if video else "(no video URL)"
    result = await provider.send_template_message(
        to=recipient,
        template_name=settings.whatsapp_template_name,
        params=[title, "Published successfully", video_url],
    )
    message_id = (result.get("messages") or [{}])[0].get("id")

    async with service_session() as session:
        await session.execute(
            text(
                "INSERT INTO notifications_sent "
                "(tenant_id, job_id, notify_channel, message_type, status, provider_message_id) "
                "VALUES (:tenant_id, :job_id, 'whatsapp', :message_type, 'sent', :message_id)"
            ),
            {
                "tenant_id": job_row["tenant_id"],
                "job_id": job_id,
                "message_type": MESSAGE_TYPE,
                "message_id": message_id,
            },
        )
        await session.commit()

    return {"sent": True, "recipient": recipient, "provider_message_id": message_id}
