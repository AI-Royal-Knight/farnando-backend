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


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_contact_ticket_emails(self: object, ticket_id: str) -> None:
    """Contact-form ticket (B2): acknowledgement to the requester and a staff notice."""

    from apps.support.models import SupportTicket

    _ = self
    ticket = SupportTicket.objects.filter(pk=ticket_id).first()
    if ticket is None:
        return
    first = ticket.messages.order_by("created_at").first()
    _send(
        to=ticket.requester_email,
        subject=f"We received your message ({ticket.reference}) — Adelgaceya",
        template="contact_ack",
        context={"full_name": ticket.requester_name, "message": first.body if first else ""},
    )
    for staff_email in settings.SUPPORT_NOTIFICATION_EMAILS:
        _send(
            to=staff_email,
            subject=f"New support ticket {ticket.reference}",
            template="ticket_staff",
            context={"reference": ticket.reference},
        )


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_contact_ticket_reply(self: object, message_id: str) -> None:
    """Contact-form requesters have no portal, so the reply text is emailed."""

    from apps.support.models import TicketMessage

    _ = self
    message = TicketMessage.objects.select_related("ticket").filter(pk=message_id).first()
    if message is None or message.is_internal:
        return
    ticket = message.ticket
    _send(
        to=ticket.requester_email,
        subject=f"Re: {ticket.subject} ({ticket.reference})",
        template="ticket_reply_contact",
        context={"full_name": ticket.requester_name, "body": message.body},
    )


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_complaint_response_email(self: object, response_id: str) -> None:
    from apps.support.models import ComplaintResponse

    _ = self
    response = ComplaintResponse.objects.select_related("complaint").filter(pk=response_id).first()
    if response is None:
        return
    complaint = response.complaint
    _send(
        to=complaint.email,
        subject=f"Respuesta a su reclamo {complaint.complaint_number}",
        template="complaint_response",
        context={
            "full_name": complaint.full_name,
            "complaint_number": complaint.complaint_number,
            "body": response.body,
        },
    )


@shared_task(acks_late=True, time_limit=300, soft_time_limit=280)  # type: ignore[untyped-decorator]
def run_support_jobs() -> dict[str, int]:
    """Close tickets left RESOLVED past `support.auto_close_resolved_days`."""

    from apps.support.tickets import auto_close_resolved

    result = {"auto_closed": auto_close_resolved()}
    logger.info("support_jobs", **result)
    return result
