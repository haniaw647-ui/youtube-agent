from celery import Celery
from celery.schedules import crontab

from src.orchestrator.config import get_settings

settings = get_settings()

celery_app = Celery(
    "youtube_automation",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["src.workers.tasks.light", "src.workers.tasks.heavy"],
)

celery_app.conf.task_routes = {
    "src.workers.tasks.light.*": {"queue": "light"},
    "src.workers.tasks.heavy.*": {"queue": "heavy"},
}

# RUNBOOK.md §1/§2: default Celery acks a task the moment a worker *receives*
# it, before running it — a worker crash mid-task (OOM, redeploy, SIGKILL)
# loses the task with no retry, orphaning the job_stages row it already
# inserted. task_acks_late defers the ack until the task actually finishes
# (success or exception), and task_reject_on_worker_lost explicitly requeues
# it if the worker dies mid-task rather than leaving it stuck until Redis's
# broker-level visibility timeout. Trade-off, accepted deliberately: if a
# worker dies *after* real work succeeded (e.g. an LLM call already spent a
# tenant's own money) but before acking, the task redelivers and reruns from
# the top — a duplicate job_stages row and a second provider API call, not
# just a no-op retry. Reliability against silent stuck jobs was judged worth
# that cost.
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True

# Phase 9 analytics pulls — beat runs embedded in worker-light (-B flag), not
# a dedicated service (see run_analytics_pull's docstring for why). Every 6
# hours is cheap: get_video_stats costs ~1 quota unit per video, nowhere near
# the shared YouTube quota ceiling that uploads have to worry about.
celery_app.conf.beat_schedule = {
    "pull-analytics-snapshots": {
        "task": "src.workers.tasks.light.run_analytics_pull",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    # Phase 10: hourly is fine-grained enough for the coarsest cadence we
    # support (daily) without hammering the DB with an unnecessary scan.
    "run-scheduled-jobs": {
        "task": "src.workers.tasks.light.run_scheduler_tick",
        "schedule": crontab(minute=0),
    },
}
