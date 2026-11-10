"""Contact-message and complaints-book business rules."""

from __future__ import annotations

import ipaddress
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from apps.catalog.models import ComplaintCategory
from apps.support.models import ComplaintBookEntry, ComplaintSequence, ContactMessage


def client_ip(request: HttpRequest) -> str | None:
    raw = request.META.get("REMOTE_ADDR")
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


class ContactMessageService:
    @staticmethod
    def create(*, data: dict[str, Any], request: HttpRequest) -> ContactMessage:
        message = ContactMessage.objects.create(
            full_name=data["full_name"].strip(),
            email=data["email"],
            message=data["message"],
            source_ip=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:1024],
        )
        transaction.on_commit(lambda: _send_contact_emails(str(message.pk)))
        return message


class ComplaintService:
    @staticmethod
    def _next_sequence(year: int) -> int:
        try:
            with transaction.atomic():
                sequence, _created = ComplaintSequence.objects.get_or_create(year=year)
        except IntegrityError:
            sequence = ComplaintSequence.objects.get(year=year)
        locked = ComplaintSequence.objects.select_for_update().get(pk=sequence.pk)
        locked.last_number += 1
        locked.save(update_fields=["last_number"])
        return locked.last_number

    @classmethod
    def create(cls, *, data: dict[str, Any], request: HttpRequest) -> ComplaintBookEntry:
        now = timezone.now()
        year = timezone.localtime(now).year
        category = ComplaintCategory.objects.get(code=data["category_code"], is_active=True)
        sequence = cls._next_sequence(year)
        entry = ComplaintBookEntry.objects.create(
            complaint_number=f"LR-{year}-{sequence:06d}",
            year=year,
            sequence=sequence,
            full_name=data["full_name"].strip(),
            email=data["email"],
            category=category,
            filing_type=data["filing_type"],
            description=data["description"],
            order_reference=data.get("order_reference", ""),
            document_type=data.get("document_type", ""),
            document_number=data.get("document_number", ""),
            phone=data.get("phone", ""),
            response_due_at=now + timedelta(hours=settings.COMPLAINT_RESPONSE_SLA_HOURS),
            source_ip=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:1024],
        )
        transaction.on_commit(lambda: _send_complaint_emails(str(entry.pk)))
        return entry


def _send_contact_emails(contact_id: str) -> None:
    from apps.support.tasks import send_contact_message_emails

    send_contact_message_emails.delay(contact_id)


def _send_complaint_emails(complaint_id: str) -> None:
    from apps.support.tasks import send_complaint_emails

    send_complaint_emails.delay(complaint_id)
