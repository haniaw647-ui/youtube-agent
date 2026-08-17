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

# Phase 9 analytics pulls — beat runs embedded in worker-light (-B flag), not
# a dedicated service (see run_analytics_pull's docstring for why). Every 6
# hours is cheap: get_video_stats costs ~1 quota unit per video, nowhere near
# the shared YouTube quota ceiling that uploads have to worry about.
celery_app.conf.beat_schedule = {
    "pull-analytics-snapshots": {
        "task": "src.workers.tasks.light.run_analytics_pull",
        "schedule": crontab(minute=0, hour="*/6"),
    },
}
