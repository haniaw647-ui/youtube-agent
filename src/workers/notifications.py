from sqlalchemy import text

from src.orchestrator.db import service_session


async def notify_job_success(job_id: str) -> None:
    """Called once a job reaches its terminal 'done' state (stage_runner's
    _mark_job_done) — the in-dashboard replacement for what the old
    whatsapp_notification pipeline stage did on the success path."""
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT tenant_id, title FROM jobs WHERE id = :id"), {"id": job_id}
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
        title = job_row["title"] or job_id
        video_url = video["url"] if video else None
        detail = f'"{title}" published successfully' + (f" — {video_url}" if video_url else "")
        await session.execute(
            text(
                "INSERT INTO notifications_sent "
                "(tenant_id, job_id, notify_channel, message_type, status, detail) "
                "VALUES (:tenant_id, :job_id, 'in_app', 'job_completed', 'delivered', :detail)"
            ),
            {"tenant_id": job_row["tenant_id"], "job_id": job_id, "detail": detail},
        )
        await session.commit()


async def notify_job_awaiting_approval(job_id: str, tenant_id: str, stage: str) -> None:
    """Called from stage_runner._mark_awaiting_approval — the single place a
    job stops for a human gate (topic_scoring, script_qa escalation, or the
    hard-coded final_qa/youtube_upload gate). Without this, a tenant only
    finds out a job is waiting by happening to check the dashboard."""
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT title FROM jobs WHERE id = :id"), {"id": job_id}
                )
            )
            .mappings()
            .first()
        )
        if job_row is None:
            return

        title = job_row["title"] or job_id
        detail = f'"{title}" needs your approval at {stage}'
        await session.execute(
            text(
                "INSERT INTO notifications_sent "
                "(tenant_id, job_id, notify_channel, message_type, status, detail) "
                "VALUES (:tenant_id, :job_id, 'in_app', 'awaiting_approval', 'delivered', "
                " :detail)"
            ),
            {"tenant_id": tenant_id, "job_id": job_id, "detail": detail},
        )
        await session.commit()


async def notify_job_failure(job_id: str, tenant_id: str, stage: str, error: str) -> None:
    """Called only once a stage's retries are truly exhausted (see the
    `self.request.retries >= self.max_retries` check in tasks/light.py and
    tasks/heavy.py) — this is the "terminal failure" moment, not just any
    single failed attempt. Also the sole place a job actually gets marked
    'failed' — preserved from the old WhatsApp-specific version of this
    function, which did the same update before it ever touched WhatsApp."""
    async with service_session() as session:
        job_row = (
            (
                await session.execute(
                    text("SELECT tenant_id, title FROM jobs WHERE id = :id"), {"id": job_id}
                )
            )
            .mappings()
            .first()
        )
        if job_row is None:
            return

        await session.execute(
            text("UPDATE jobs SET overall_status = 'failed' WHERE id = :id"), {"id": job_id}
        )

        title = job_row["title"] or job_id
        detail = f'"{title}" failed at {stage}: {error[:200]}'
        await session.execute(
            text(
                "INSERT INTO notifications_sent "
                "(tenant_id, job_id, notify_channel, message_type, status, detail) "
                "VALUES (:tenant_id, :job_id, 'in_app', 'job_failed', 'delivered', :detail)"
            ),
            {"tenant_id": job_row["tenant_id"], "job_id": job_id, "detail": detail},
        )
        await session.commit()
