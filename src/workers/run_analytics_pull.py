"""Entry point for the Railway cron service (Phase 9): runs once, pulls any
due 1/7/30-day analytics snapshots, and exits. Not a Celery task — this is a
short-lived batch job, so there's no benefit to routing it through the
broker/worker fleet, and it stays resilient to workers being briefly down.
"""

import asyncio
import logging

from src.workers.analytics import pull_due_snapshots

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    result = asyncio.run(pull_due_snapshots())
    logger.info("analytics pull complete: %s", result)


if __name__ == "__main__":
    main()
