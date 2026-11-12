"""Staff and admin dashboards (Part 5, section 3) and the audit-log list (11.1)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.assessments.enums import EvaluationStatus
from apps.backoffice import reports
from apps.backoffice.models import PrivacyRequest, PrivacyRequestStatus
from apps.consultations.models import ClinicianProfile, PhysicianVerificationStatus
from apps.core.models import AuditLog
from apps.staff import commerce
from apps.staff import evaluations as staff_evaluations
from apps.staff.models import Permission
from apps.staff.permissions import has_perm
from apps.support import complaints, tickets
from apps.support.models import SupportTicket, TicketStatus

LIMA = ZoneInfo("America/Lima")
OPEN_TICKET_STATES = (TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER)


def _unassigned() -> QuerySet[Any]:
    return staff_evaluations.filtered({"assignment": "UNASSIGNED"}).filter(
        status=EvaluationStatus.SUBMITTED
    )


def staff_stats(viewer: User) -> dict[str, Any]:
    can_evaluations = has_perm(viewer, Permission.EVALUATIONS_READ)
    can_tickets = has_perm(viewer, Permission.TICKETS_MANAGE)
    can_orders = has_perm(viewer, Permission.ORDERS_READ)
    recent = SupportTicket.objects.filter(created_at__gte=timezone.now() - timedelta(days=30))
    return {
        "unassigned_evaluations_count": _unassigned().count() if can_evaluations else None,
        "open_tickets_count": (
            SupportTicket.objects.filter(status__in=OPEN_TICKET_STATES).count()
            if can_tickets
            else None
        ),
        "orders_processing_count": commerce.order_summary()["processing"] if can_orders else None,
        "avg_first_response_minutes": (
            tickets.avg_first_response_minutes(recent) if can_tickets else None
        ),
    }


def staff_dashboard(viewer: User) -> dict[str, Any]:
    unassigned: list[dict[str, Any]] = []
    if has_perm(viewer, Permission.EVALUATIONS_READ):
        rows = list(_unassigned().order_by("submitted_at")[:5])
        unassigned = staff_evaluations.list_payloads(rows)
    urgent: list[dict[str, Any]] = []
    if has_perm(viewer, Permission.TICKETS_MANAGE):
        urgent = tickets.urgent_for_dashboard(viewer, limit=5)
    return {
        "stats": staff_stats(viewer),
        "unassigned_evaluations": unassigned,
        "urgent_tickets": urgent,
    }


def admin_dashboard(viewer: User) -> dict[str, Any]:
    return {
        "stats": staff_stats(viewer),
        "pending_physician_verifications_count": ClinicianProfile.objects.filter(
            verification_status=PhysicianVerificationStatus.PENDING
        ).count(),
        "open_complaints_count": complaints.summary()["open"],
        "overdue_complaints_count": complaints.summary()["overdue"],
        "open_privacy_requests_count": PrivacyRequest.objects.filter(
            status__in=(PrivacyRequestStatus.RECEIVED, PrivacyRequestStatus.IN_PROGRESS)
        ).count(),
        "kpis": reports.last_30_days_kpis(),
    }


# ---------------------------------------------------------------------------
# Audit log (11.1)
# ---------------------------------------------------------------------------


def audit_logs(params: Any) -> QuerySet[AuditLog]:
    queryset = AuditLog.objects.select_related("actor")
    for param, field in (
        ("actor_id", "actor_id"),
        ("action", "action_type"),
        ("object_type", "object_type"),
        ("object_id", "object_id"),
        ("patient_id", "patient_id"),
    ):
        if params.get(param):
            queryset = queryset.filter(**{field: params[param]})
    if params.get("action") and params["action"] not in AuditLog.ActionType.values:
        raise ValidationError({"action": ["Unknown action."]})
    for name, lookup in (("from", "created_at__gte"), ("to", "created_at__lt")):
        raw = params.get(name)
        if not raw:
            continue
        try:
            day = date.fromisoformat(str(raw))
        except ValueError as exc:
            raise ValidationError({name: ["Use YYYY-MM-DD."]}) from exc
        if name == "to":
            day += timedelta(days=1)
        bound = datetime.combine(day, time.min, tzinfo=LIMA)
        queryset = queryset.filter(**{lookup: bound})
    return queryset.order_by("-created_at")


def audit_payload(entry: AuditLog) -> dict[str, Any]:
    actor = entry.actor
    return {
        "id": entry.pk,
        "at": entry.created_at,
        "actor": (
            {"id": actor.pk, "name": actor.get_full_name(), "role": actor.role} if actor else None
        ),
        "action": entry.action_type,
        "object_type": entry.object_type,
        "object_id": entry.object_id,
        "patient_id": entry.patient_id,
        "ip_address": entry.ip_address,
        "user_agent": entry.user_agent,
        "request_id": entry.request_id,
        "changed_fields": list(entry.changed_fields or []),
    }
