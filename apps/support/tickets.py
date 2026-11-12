"""Support tickets for patients, the contact form and staff (contract Part 5, 6.1-6.3)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Avg, DurationField, ExpressionWrapper, F, Q, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.catalog import runtime
from apps.catalog.models import TicketCategory
from apps.core.audit import record_audit
from apps.core.exceptions import InvalidStateTransition
from apps.staff.models import Permission
from apps.staff.permissions import has_clinical_access, has_perm
from apps.support.models import (
    SupportTicket,
    TicketHistory,
    TicketMessage,
    TicketPriority,
    TicketSequence,
    TicketSource,
    TicketStatus,
)

RESTRICTED_SUBJECT = "Clinical: restricted"
STAFF_TRANSITIONS: dict[str, set[str]] = {
    TicketStatus.OPEN: {TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED},
    TicketStatus.IN_PROGRESS: {
        TicketStatus.OPEN,
        TicketStatus.AWAITING_CUSTOMER,
        TicketStatus.RESOLVED,
    },
    TicketStatus.AWAITING_CUSTOMER: {TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED},
    TicketStatus.RESOLVED: {TicketStatus.CLOSED, TicketStatus.OPEN},
    TicketStatus.CLOSED: set(),
}


def clean_text(value: str, *, field: str, min_length: int = 1, max_length: int = 4000) -> str:
    cleaned = strip_tags(value or "").strip()
    if len(cleaned) < min_length:
        raise ValidationError({field: [f"Enter at least {min_length} characters."]})
    if len(cleaned) > max_length:
        raise ValidationError({field: [f"At most {max_length} characters."]})
    return cleaned


def _next_reference(year: int) -> str:
    try:
        with transaction.atomic():
            sequence, _ = TicketSequence.objects.get_or_create(year=year)
    except IntegrityError:
        sequence = TicketSequence.objects.get(year=year)
    locked = TicketSequence.objects.select_for_update().get(pk=sequence.pk)
    locked.last_number += 1
    locked.save(update_fields=["last_number"])
    return f"TCK-{year}-{locked.last_number:06d}"


def _history(ticket: SupportTicket, event: str, old: Any, new: Any, actor: Any) -> None:
    TicketHistory.objects.create(
        ticket=ticket,
        event=event,
        from_value=str(old or "")[:64],
        to_value=str(new or "")[:64],
        actor=actor if getattr(actor, "is_authenticated", False) else None,
    )


def create_ticket(
    *,
    subject: str,
    category: TicketCategory,
    source: str,
    requester_name: str,
    requester_email: str,
    body: str,
    patient: User | None = None,
    order: Any = None,
    author: User | None = None,
    author_role: str = "REQUESTER",
    is_internal: bool = False,
    source_ip: str | None = None,
) -> SupportTicket:
    with transaction.atomic():
        year = timezone.localdate().year
        ticket = SupportTicket.objects.create(
            reference=_next_reference(year),
            subject=subject[:120],
            category=category,
            source=source,
            requester_name=requester_name[:120],
            requester_email=requester_email,
            patient=patient,
            order=order,
            source_ip=source_ip,
        )
        TicketMessage.objects.create(
            ticket=ticket,
            author=author,
            author_role=author_role,
            body=body,
            is_internal=is_internal,
        )
        _history(ticket, "CREATED", "", ticket.status, author)
    return ticket


def category_by_code(code: str) -> TicketCategory:
    category = TicketCategory.objects.filter(code=code, is_active=True).first()
    if category is None:
        raise ValidationError({"category_code": ["Unknown category."]})
    return category


def create_contact_ticket(*, data: dict[str, Any], source_ip: str | None) -> SupportTicket:
    """Amendment B2: the public contact form opens a ticket."""

    category = TicketCategory.objects.filter(code="OTHER").first() or TicketCategory.objects.create(
        code="OTHER", label="Other"
    )
    ticket = create_ticket(
        subject="Contact form message",
        category=category,
        source=TicketSource.CONTACT_FORM,
        requester_name=data["full_name"].strip(),
        requester_email=data["email"],
        body=data["message"],
        source_ip=source_ip,
    )
    from apps.support.tasks import send_contact_ticket_emails

    ticket_id = str(ticket.pk)
    transaction.on_commit(lambda: send_contact_ticket_emails.delay(ticket_id))
    return ticket


def create_refund_task(*, title: str, order: Any = None, patient: User | None = None) -> None:
    """Internal BILLING ticket used as the refund task (Part 5, 7.1 and 7.3)."""

    from apps.clinical.tasks import send_staff_alert

    category = TicketCategory.objects.filter(
        code="BILLING"
    ).first() or TicketCategory.objects.create(code="BILLING", label="Billing")
    create_ticket(
        subject=title,
        category=category,
        source=TicketSource.STAFF,
        requester_name=patient.get_full_name() if patient else "Adelgaceya",
        requester_email=patient.email if patient else "support@adelgaceya.pe",
        body=f"{title}. Process the refund from the payment (ORDERS_REFUND).",
        patient=None,
        order=order,
        author=None,
        author_role="STAFF",
        is_internal=True,
    )
    transaction.on_commit(lambda: send_staff_alert.delay("refund_task_created"))


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def is_restricted(ticket: SupportTicket, viewer: Any) -> bool:
    if not ticket.category.requires_clinical_staff:
        return False
    if getattr(viewer, "role", None) in (Role.PATIENT,):
        return False
    return not has_clinical_access(viewer)


def ticket_payload(ticket: SupportTicket, *, viewer: Any = None) -> dict[str, Any]:
    restricted = viewer is not None and is_restricted(ticket, viewer)
    category = ticket.category
    return {
        "id": ticket.pk,
        "reference": ticket.reference,
        "subject": RESTRICTED_SUBJECT if restricted else ticket.subject,
        "status": ticket.status,
        "priority": ticket.priority,
        "category": {
            "code": category.code,
            "label": category.label,
            "requires_clinical_staff": category.requires_clinical_staff,
        },
        "source": ticket.source,
        "requester": {
            "name": ticket.requester_name,
            "email": ticket.requester_email,
            "patient_id": ticket.patient_id,
        },
        "order_id": ticket.order_id,
        "assigned_to": (
            {"id": ticket.assigned_to.pk, "name": ticket.assigned_to.get_full_name()}
            if ticket.assigned_to
            else None
        ),
        "first_response_at": ticket.first_response_at,
        "resolved_at": ticket.resolved_at,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
    }


def message_payload(message: TicketMessage) -> dict[str, Any]:
    author = message.author
    if message.author_role == "REQUESTER":
        name = author.get_full_name() if author else message.ticket.requester_name
    else:
        name = author.get_full_name() if author else "Adelgaceya"
    return {
        "id": message.pk,
        "author": {"id": author.pk if author else None, "name": name, "role": message.author_role},
        "body": message.body,
        "is_internal": message.is_internal,
        "created_at": message.created_at,
    }


def base() -> QuerySet[SupportTicket]:
    return SupportTicket.objects.select_related("category", "assigned_to", "patient")


# ---------------------------------------------------------------------------
# Patient side (6.2)
# ---------------------------------------------------------------------------


def patient_tickets(user: User) -> QuerySet[SupportTicket]:
    return base().filter(patient=user)


def patient_ticket(user: User, ticket_id: Any) -> SupportTicket:
    ticket = patient_tickets(user).filter(pk=ticket_id).first()
    if ticket is None:
        raise NotFound()
    return ticket


def create_patient_ticket(*, user: User, data: dict[str, Any]) -> SupportTicket:
    from apps.orders.models import Order

    category = category_by_code(data["category_code"])
    order = None
    if data.get("order_id"):
        order = Order.objects.filter(pk=data["order_id"], user=user).first()
        if order is None:
            raise ValidationError({"order_id": ["Unknown order."]})
    return create_ticket(
        subject=clean_text(data["subject"], field="subject", min_length=3, max_length=120),
        category=category,
        source=TicketSource.PATIENT,
        requester_name=user.get_full_name(),
        requester_email=user.email,
        body=clean_text(data["message"], field="message", min_length=10),
        patient=user,
        order=order,
        author=user,
    )


def patient_message(*, ticket: SupportTicket, user: User, body: str) -> TicketMessage:
    with transaction.atomic():
        locked = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        if locked.status == TicketStatus.CLOSED:
            raise InvalidStateTransition()
        message = TicketMessage.objects.create(
            ticket=locked,
            author=user,
            author_role="REQUESTER",
            body=clean_text(body, field="body"),
        )
        if locked.status in (TicketStatus.AWAITING_CUSTOMER, TicketStatus.RESOLVED):
            _history(locked, "STATUS", locked.status, TicketStatus.OPEN, user)
            locked.status = TicketStatus.OPEN
            locked.resolved_at = None
        locked.save()
    return message


def patient_close(*, ticket: SupportTicket, user: User) -> SupportTicket:
    with transaction.atomic():
        locked = SupportTicket.objects.select_for_update().get(pk=ticket.pk)
        if locked.status == TicketStatus.CLOSED:
            raise InvalidStateTransition()
        _history(locked, "STATUS", locked.status, TicketStatus.CLOSED, user)
        locked.status = TicketStatus.CLOSED
        locked.closed_at = timezone.now()
        locked.save()
    return base().get(pk=ticket.pk)


# ---------------------------------------------------------------------------
# Staff side (6.3)
# ---------------------------------------------------------------------------


def _local_bound(value: Any, *, end: bool) -> datetime:
    moment = datetime.combine(value + timedelta(days=1) if end else value, time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


def staff_filtered(params: dict[str, Any], *, viewer: User) -> QuerySet[SupportTicket]:
    queryset = base()
    for field in ("status", "priority", "source"):
        if params.get(field):
            queryset = queryset.filter(**{field: params[field]})
    if params.get("category_code"):
        queryset = queryset.filter(category__code=params["category_code"])
    assigned = params.get("assigned_to")
    if assigned == "me":
        queryset = queryset.filter(assigned_to=viewer)
    elif assigned == "unassigned":
        queryset = queryset.filter(assigned_to__isnull=True)
    elif assigned:
        queryset = queryset.filter(assigned_to_id=assigned)
    if params.get("search"):
        term = params["search"]
        condition = (
            Q(requester_name__icontains=term)
            | Q(requester_email__icontains=term)
            | Q(reference__icontains=term)
        )
        if has_clinical_access(viewer):
            condition |= Q(subject__icontains=term)
        else:
            condition |= Q(subject__icontains=term, category__requires_clinical_staff=False)
        queryset = queryset.filter(condition)
    if params.get("from"):
        queryset = queryset.filter(created_at__gte=_local_bound(params["from"], end=False))
    if params.get("to"):
        queryset = queryset.filter(created_at__lt=_local_bound(params["to"], end=True))
    if params.get("ordering") == "priority":
        return queryset.order_by("-priority", "-created_at")
    return queryset.order_by("-created_at")


def avg_first_response_minutes(queryset: QuerySet[SupportTicket]) -> int | None:
    result = (
        queryset.filter(first_response_at__isnull=False)
        .annotate(
            wait=ExpressionWrapper(
                F("first_response_at") - F("created_at"), output_field=DurationField()
            )
        )
        .aggregate(avg=Avg("wait"))
    )
    if result["avg"] is None:
        return None
    minutes: float = result["avg"].total_seconds() / 60
    return round(minutes)


def staff_summary(queryset: QuerySet[SupportTicket]) -> dict[str, Any]:
    today = timezone.localdate()
    return {
        "total": queryset.count(),
        "open": queryset.filter(status=TicketStatus.OPEN).count(),
        "in_progress": queryset.filter(status=TicketStatus.IN_PROGRESS).count(),
        "avg_first_response_minutes": avg_first_response_minutes(queryset),
        "resolved_today": queryset.filter(
            resolved_at__gte=_local_bound(today, end=False),
            resolved_at__lt=_local_bound(today, end=True),
        ).count(),
    }


def staff_ticket(ticket_id: Any, viewer: User, *, lock: bool = False) -> SupportTicket:
    queryset = base()
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    ticket = queryset.filter(pk=ticket_id).first()
    if ticket is None or is_restricted(ticket, viewer):
        raise NotFound()
    return ticket


def staff_detail(ticket: SupportTicket, *, viewer: User, view: str, request: Any) -> dict[str, Any]:
    body = ticket_payload(ticket, viewer=viewer)
    body["messages"] = [
        message_payload(message)
        for message in ticket.messages.select_related("author", "ticket").order_by("created_at")
    ]
    if view != "CHAT":
        body["history"] = [
            {
                "event": entry.event,
                "from": entry.from_value or None,
                "to": entry.to_value or None,
                "actor_name": entry.actor.get_full_name() if entry.actor else None,
                "at": entry.created_at,
            }
            for entry in ticket.history.select_related("actor").order_by("created_at")
        ]
    record_audit(
        actor=viewer,
        action="ticket.viewed",
        object_type="SupportTicket",
        object_id=ticket.pk,
        request=request,
        patient_id=ticket.patient_id,
    )
    return body


def assign(*, ticket_id: Any, staff_id: Any, viewer: User, request: Any) -> SupportTicket:
    with transaction.atomic():
        ticket = staff_ticket(ticket_id, viewer, lock=True)
        assignee = None
        if staff_id is not None:
            assignee = User.objects.filter(
                pk=staff_id, role__in=(Role.STAFF, Role.ADMIN), is_active=True
            ).first()
            if assignee is None or not has_perm(assignee, Permission.TICKETS_MANAGE):
                raise ValidationError({"staff_id": ["This person cannot manage tickets."]})
            if ticket.category.requires_clinical_staff and not has_clinical_access(assignee):
                raise ValidationError({"staff_id": ["Clinical tickets need clinical staff."]})
        _history(ticket, "ASSIGNED", ticket.assigned_to_id, getattr(assignee, "pk", ""), viewer)
        ticket.assigned_to = assignee
        ticket.save(update_fields=["assigned_to", "updated_at"])
        record_audit(
            actor=viewer,
            action="ticket.assigned",
            object_type="SupportTicket",
            object_id=ticket.pk,
            request=request,
        )
    return base().get(pk=ticket.pk)


def reply(
    *, ticket_id: Any, body: str, is_internal: bool, viewer: User, request: Any
) -> TicketMessage:
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    with transaction.atomic():
        ticket = staff_ticket(ticket_id, viewer, lock=True)
        if ticket.status == TicketStatus.CLOSED:
            raise InvalidStateTransition()
        message = TicketMessage.objects.create(
            ticket=ticket,
            author=viewer,
            author_role="STAFF",
            body=clean_text(body, field="body"),
            is_internal=is_internal,
        )
        fields = ["updated_at"]
        if not is_internal:
            if ticket.first_response_at is None:
                ticket.first_response_at = message.created_at
                fields.append("first_response_at")
            if ticket.status == TicketStatus.OPEN:
                _history(ticket, "STATUS", ticket.status, TicketStatus.AWAITING_CUSTOMER, viewer)
                ticket.status = TicketStatus.AWAITING_CUSTOMER
                fields.append("status")
            if ticket.source == TicketSource.CONTACT_FORM or ticket.patient is None:
                from apps.support.tasks import send_contact_ticket_reply

                message_id = str(message.pk)
                transaction.on_commit(lambda: send_contact_ticket_reply.delay(message_id))
            else:
                notify(
                    ticket.patient,
                    NotificationType.SUPPORT_TICKET_REPLY,
                    target_kind="support_ticket",
                    target_id=ticket.pk,
                )
        ticket.save(update_fields=fields)
        record_audit(
            actor=viewer,
            action="ticket.replied",
            object_type="SupportTicket",
            object_id=ticket.pk,
            request=request,
            metadata={"internal": is_internal},
        )
    return message


def set_status(*, ticket_id: Any, status: str, viewer: User, request: Any) -> SupportTicket:
    with transaction.atomic():
        ticket = staff_ticket(ticket_id, viewer, lock=True)
        if status not in STAFF_TRANSITIONS.get(ticket.status, set()):
            raise InvalidStateTransition()
        _history(ticket, "STATUS", ticket.status, status, viewer)
        now = timezone.now()
        ticket.status = status
        if status == TicketStatus.RESOLVED:
            ticket.resolved_at = now
        elif status == TicketStatus.OPEN:
            ticket.resolved_at = None
        elif status == TicketStatus.CLOSED:
            ticket.closed_at = now
        ticket.save()
        record_audit(
            actor=viewer,
            action="ticket.status_changed",
            object_type="SupportTicket",
            object_id=ticket.pk,
            request=request,
            changed_fields=["status"],
        )
    return base().get(pk=ticket.pk)


def set_priority(*, ticket_id: Any, priority: str, viewer: User, request: Any) -> SupportTicket:
    with transaction.atomic():
        ticket = staff_ticket(ticket_id, viewer, lock=True)
        _history(ticket, "PRIORITY", ticket.priority, priority, viewer)
        ticket.priority = priority
        ticket.save(update_fields=["priority", "updated_at"])
        record_audit(
            actor=viewer,
            action="ticket.priority_changed",
            object_type="SupportTicket",
            object_id=ticket.pk,
            request=request,
            changed_fields=["priority"],
        )
    return base().get(pk=ticket.pk)


def auto_close_resolved(now: datetime | None = None) -> int:
    now = now or timezone.now()
    days = runtime.hours("support", "auto_close_resolved_days")
    cutoff = now - timedelta(days=days)
    closed = 0
    for ticket in SupportTicket.objects.filter(
        status=TicketStatus.RESOLVED, resolved_at__lt=cutoff
    ):
        _history(ticket, "STATUS", ticket.status, TicketStatus.CLOSED, None)
        ticket.status = TicketStatus.CLOSED
        ticket.closed_at = now
        ticket.save(update_fields=["status", "closed_at", "updated_at"])
        closed += 1
    return closed


def urgent_for_dashboard(viewer: User, limit: int = 5) -> list[dict[str, Any]]:
    rows = []
    open_statuses = (TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER)
    for ticket in (
        base()
        .filter(priority=TicketPriority.URGENT, status__in=open_statuses)
        .order_by("-created_at")[:limit]
    ):
        restricted = is_restricted(ticket, viewer)
        first = ticket.messages.filter(is_internal=False).order_by("created_at").first()
        rows.append(
            {
                "id": ticket.pk,
                "subject": RESTRICTED_SUBJECT if restricted else ticket.subject,
                "requester_name": ticket.requester_name,
                "category": {"code": ticket.category.code, "label": ticket.category.label},
                "excerpt": "" if restricted or first is None else first.body[:140],
                "created_at": ticket.created_at,
                "priority": ticket.priority,
            }
        )
    return rows
