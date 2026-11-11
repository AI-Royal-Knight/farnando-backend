"""Notification email delivery. Arguments are ids only; copy is generic."""

from __future__ import annotations

import structlog
from django.conf import settings

from apps.core.email import get_email_service
from apps.notifications.models import Notification
from celery import shared_task

logger = structlog.get_logger(__name__)


class NotificationEmailError(RuntimeError):
    pass


@shared_task(  # type: ignore[untyped-decorator]
    bind=True,
    autoretry_for=(NotificationEmailError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
    time_limit=60,
    soft_time_limit=45,
)
def send_notification_email(self: object, notification_id: str) -> None:
    _ = self
    notification = Notification.objects.select_related("user").filter(pk=notification_id).first()
    if notification is None or not notification.user.is_active:
        return
    context: dict[str, object] = {
        "full_name": notification.user.get_short_name(),
        "title": notification.title,
        "body": notification.body,
        "app_url": settings.FRONTEND_URL,
    }
    try:
        get_email_service().send(
            to=notification.user.email,
            subject=notification.title,
            template="notification",
            context=context,
        )
    except Exception as exc:
        logger.warning("notification_email_failed", error=type(exc).__name__)
        raise NotificationEmailError("Notification email delivery failed.") from None
