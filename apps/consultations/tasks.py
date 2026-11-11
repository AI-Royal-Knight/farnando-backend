"""Scheduled consultation housekeeping (Celery beat, every 5 minutes)."""

from __future__ import annotations

import structlog

from apps.consultations import services
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(acks_late=True, time_limit=240, soft_time_limit=200)  # type: ignore[untyped-decorator]
def run_consultation_jobs() -> dict[str, int]:
    result = {
        "expired": services.expire_requests(),
        "no_show": services.mark_no_shows(),
        "reminders": services.send_reminders(),
    }
    logger.info("consultation_jobs", **result)
    return result
