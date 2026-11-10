"""Sanitised support email tasks. Arguments are IDs only."""

from __future__ import annotations

import structlog
from django.conf import settings

from apps.core.email import get_email_service
from apps.support.models import ComplaintBookEntry, ContactMessage
from celery import shared_task

logger = structlog.get_logger(__name__)


class EmailDeliveryError(RuntimeError):
    pass


_TASK_OPTIONS = {
    "bind": True,
    "autoretry_for": (EmailDeliveryError,),
    "retry_backoff": True,
    "retry_jitter": True,
    "max_retries": 5,
    "acks_late": True,
    "time_limit": 60,
    "soft_time_limit": 45,
}


def _send(*, to: str, subject: str, template: str, context: dict[str, object]) -> None:
    try:
        get_email_service().send(to=to, subject=subject, template=template, context=context)
    except Exception as exc:
        logger.warning("support_email_failed", template=template, error=type(exc).__name__)
        raise EmailDeliveryError("Transactional email delivery failed.") from None


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_contact_message_emails(self: object, contact_id: str) -> None:
    _ = self
    message = ContactMessage.objects.filter(pk=contact_id).first()
    if message is None:
        return
    _send(
        to=message.email,
        subject="We received your message — Adelgaceya",
        template="contact_ack",
        context={"full_name": message.full_name, "message": message.message},
    )
    for staff_email in settings.SUPPORT_NOTIFICATION_EMAILS:
        _send(
            to=staff_email,
            subject=f"New contact message {message.pk}",
            template="contact_staff",
            context={"message": message},
        )
    logger.info("contact_emails_sent", contact_id=str(message.pk))


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_complaint_emails(self: object, complaint_id: str) -> None:
    _ = self
    complaint = ComplaintBookEntry.objects.filter(pk=complaint_id).first()
    if complaint is None:
        return
    _send(
        to=complaint.email,
        subject=f"Complaint {complaint.complaint_number} received",
        template="complaint_ack",
        context={
            "full_name": complaint.full_name,
            "complaint_number": complaint.complaint_number,
            "response_due_at": complaint.response_due_at,
        },
    )
    for staff_email in settings.SUPPORT_NOTIFICATION_EMAILS:
        _send(
            to=staff_email,
            subject=f"New complaints-book entry {complaint.complaint_number}",
            template="complaint_staff",
            context={"complaint": complaint},
        )
    logger.info("complaint_emails_sent", complaint_id=str(complaint.pk))
