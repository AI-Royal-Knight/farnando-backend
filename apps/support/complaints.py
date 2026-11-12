"""Complaints Book handling for staff (contract Part 5, 6.4). Records are append-only."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound

from apps.accounts.models import User
from apps.core.audit import record_audit
from apps.core.exceptions import InvalidStateTransition
from apps.support.models import ComplaintBookEntry, ComplaintHistory, ComplaintResponse

Status = ComplaintBookEntry.Status
FINAL = (Status.RESOLVED, Status.CLOSED)


def _overdue_q(now: datetime) -> Q:
    return Q(response_due_at__lt=now, responded_at__isnull=True) & ~Q(status__in=FINAL)


def is_overdue(entry: ComplaintBookEntry, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    return entry.response_due_at < now and entry.responded_at is None and entry.status not in FINAL


def _bound(value: Any, *, end: bool) -> datetime:
    moment = datetime.combine(value + timedelta(days=1) if end else value, time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


def filtered(params: dict[str, Any]) -> QuerySet[ComplaintBookEntry]:
    now = timezone.now()
    queryset = ComplaintBookEntry.objects.select_related("category")
    if params.get("status"):
        queryset = queryset.filter(status=params["status"])
    if params.get("filing_type"):
        queryset = queryset.filter(filing_type=params["filing_type"])
    if params.get("overdue") is not None:
        condition = _overdue_q(now)
        queryset = queryset.filter(condition if params["overdue"] else ~condition)
    if params.get("category_code"):
        queryset = queryset.filter(category__code=params["category_code"])
    if params.get("search"):
        term = params["search"]
        queryset = queryset.filter(
            Q(complaint_number__icontains=term)
            | Q(full_name__icontains=term)
            | Q(email__icontains=term)
        )
    if params.get("from"):
        queryset = queryset.filter(created_at__gte=_bound(params["from"], end=False))
    if params.get("to"):
        queryset = queryset.filter(created_at__lt=_bound(params["to"], end=True))
    return queryset.order_by("-created_at")


def summary() -> dict[str, int]:
    now = timezone.now()
    queryset = ComplaintBookEntry.objects.all()
    return {
        "open": queryset.exclude(status__in=FINAL).count(),
        "overdue": queryset.filter(_overdue_q(now)).count(),
    }


def item_payload(entry: ComplaintBookEntry) -> dict[str, Any]:
    return {
        "complaint_number": entry.complaint_number,
        "status": entry.status,
        "filing_type": entry.filing_type,
        "category": {"code": entry.category.code, "label": entry.category.label},
        "full_name": entry.full_name,
        "email": entry.email,
        "created_at": entry.created_at,
        "response_due_at": entry.response_due_at,
        "responded_at": entry.responded_at,
        "is_overdue": is_overdue(entry),
    }


def get(complaint_number: str, *, lock: bool = False) -> ComplaintBookEntry:
    queryset = ComplaintBookEntry.objects.select_related("category")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    entry = queryset.filter(complaint_number=complaint_number).first()
    if entry is None:
        raise NotFound()
    return entry


def detail_payload(entry: ComplaintBookEntry, *, viewer: User, request: Any) -> dict[str, Any]:
    body = item_payload(entry)
    body.update(
        {
            "description": entry.description,
            "order_reference": entry.order_reference or None,
            "document_type": entry.document_type or None,
            "document_number": entry.document_number or None,
            "phone": entry.phone or None,
            "responses": [
                {
                    "id": response.pk,
                    "body": response.body,
                    "resolution": response.resolution,
                    "author_name": response.author.get_full_name(),
                    "created_at": response.created_at,
                }
                for response in entry.responses.select_related("author")
            ],
            "history": [
                {
                    "from": item.from_status or None,
                    "to": item.to_status,
                    "note": item.note or None,
                    "actor_name": item.actor.get_full_name() if item.actor else None,
                    "at": item.created_at,
                }
                for item in entry.history.select_related("actor")
            ],
        }
    )
    record_audit(
        actor=viewer,
        action="complaint.viewed",
        object_type="ComplaintBookEntry",
        object_id=entry.pk,
        request=request,
    )
    return body


def _move(entry: ComplaintBookEntry, status: str, *, actor: User, note: str = "") -> None:
    if entry.status == status:
        return
    ComplaintHistory.objects.create(
        complaint=entry, from_status=entry.status, to_status=status, note=note, actor=actor
    )
    entry.status = status
    entry.save(update_fields=["status", "updated_at"])


def respond(
    *, complaint_number: str, body: str, resolution: str, viewer: User, request: Any
) -> ComplaintResponse:
    from apps.support.tasks import send_complaint_response_email

    with transaction.atomic():
        entry = get(complaint_number, lock=True)
        if entry.status == Status.CLOSED:
            raise InvalidStateTransition()
        response = ComplaintResponse.objects.create(
            complaint=entry, body=strip_tags(body).strip(), resolution=resolution, author=viewer
        )
        if entry.responded_at is None:
            entry.responded_at = response.created_at
            entry.response_text = response.body
            entry.save(update_fields=["responded_at", "response_text", "updated_at"])
        _move(entry, resolution, actor=viewer, note="Response sent")
        record_audit(
            actor=viewer,
            action="complaint.responded",
            object_type="ComplaintBookEntry",
            object_id=entry.pk,
            request=request,
        )
        response_id = str(response.pk)
        transaction.on_commit(lambda: send_complaint_response_email.delay(response_id))
    return response


def set_status(
    *, complaint_number: str, status: str, note: str, viewer: User, request: Any
) -> ComplaintBookEntry:
    with transaction.atomic():
        entry = get(complaint_number, lock=True)
        if entry.status == Status.CLOSED or entry.status == status:
            raise InvalidStateTransition()
        if status == Status.CLOSED and not entry.responses.exists():
            raise InvalidStateTransition("Respond to the complaint before closing it.")
        _move(entry, status, actor=viewer, note=strip_tags(note or "").strip())
        record_audit(
            actor=viewer,
            action="complaint.status_changed",
            object_type="ComplaintBookEntry",
            object_id=entry.pk,
            request=request,
            changed_fields=["status"],
        )
    return entry
