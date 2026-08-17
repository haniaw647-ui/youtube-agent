import asyncio

from src.workers.celery_app import celery_app
from src.workers.stage_runner import execute_stage


@celery_app.task(
    name="src.workers.tasks.light.run_light_stage",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def run_light_stage(job_id: str, tenant_id: str, stage: str) -> None:
    asyncio.run(execute_stage(job_id, tenant_id, stage))
