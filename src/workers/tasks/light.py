import asyncio

from src.workers.celery_app import celery_app
from src.workers.failure_notify import notify_job_failure
from src.workers.stage_runner import execute_stage


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
