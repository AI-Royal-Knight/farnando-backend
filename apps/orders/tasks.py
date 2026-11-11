"""Scheduled subscription renewals, retries, period-end cancellation, auto-resume."""

from __future__ import annotations

import structlog

from apps.orders import services
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(acks_late=True, time_limit=600, soft_time_limit=540)  # type: ignore[untyped-decorator]
def run_subscription_jobs() -> dict[str, int]:
    result = services.run_subscription_jobs()
    logger.info("subscription_jobs", **result)
    return result
