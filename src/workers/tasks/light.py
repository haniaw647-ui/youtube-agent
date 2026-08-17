import asyncio
import logging

from src.workers.analytics import pull_due_snapshots
from src.workers.celery_app import celery_app
from src.workers.failure_notify import notify_job_failure
from src.workers.stage_runner import execute_stage

logger = logging.getLogger(__name__)


@celery_app.task(
    name="src.workers.tasks.light.run_light_stage",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def run_light_stage(self, job_id: str, tenant_id: str, stage: str) -> None:
    try:
        asyncio.run(execute_stage(job_id, tenant_id, stage))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            asyncio.run(notify_job_failure(job_id, tenant_id, stage, str(exc)))
        raise


@celery_app.task(name="src.workers.tasks.light.run_analytics_pull")
def run_analytics_pull() -> None:
    """Triggered by Celery Beat (embedded in worker-light via the -B flag —
    Phase 9 wanted a dedicated cron service, but the Railway project's plan
    caps it at 4 services, so this rides along on an existing one instead).
    Idempotent: due_snapshot_days() only returns marks not already captured,
    so a missed or doubled-up tick can't duplicate or lose a snapshot."""
    result = asyncio.run(pull_due_snapshots())
    logger.info("analytics pull complete: %s", result)
