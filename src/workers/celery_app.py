from celery import Celery

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
