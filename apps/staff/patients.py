"""Staff patient management (contract Part 5, section 4)."""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db import transaction
from django.db.models import (
    Case,
    CharField,
    Count,
    Exists,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.enums import EvaluationStatus
from apps.assessments.models import AssessmentMeasurement, Evaluation
from apps.assessments.services import age_on
from apps.backoffice.physicians import eligible_physician
from apps.clinical.models import ClinicalNote, NoteKind
from apps.consultations.clinicians import physician_summary
from apps.consultations.models import Consultation
from apps.core.audit import record_audit
from apps.orders.models import (
    OPEN_SUBSCRIPTION_STATUSES,
    Order,
    OrderStatusHistory,
    Subscription,
    SubscriptionStatus,
)
from apps.patients.models import PatientProfile
from apps.patients.serializers import AddressSerializer
from apps.patients.services import get_or_create_profile, latest_measurement
from apps.staff.accounts import reactivate_user, suspend_user
from apps.staff.models import Permission
from apps.staff.permissions import has_perm
from apps.treatments.models import Treatment, TreatmentStatus

STATUS_LABELS = {
    "PENDING": "Pending",
    "ACTIVE": "Active",
    "NOT_SUITABLE": "Not suitable",
    "INACTIVE": "Inactive",
    "SUSPENDED": "Suspended",
}
SEX_LABELS = {"MALE": "Male", "FEMALE": "Female", "OTHER": "Other"}


def annotated() -> QuerySet[User]:
    active_treatment = Treatment.objects.filter(
        patient=OuterRef("pk"), status=TreatmentStatus.ACTIVE
    )
    open_subscription = Subscription.objects.filter(
        user=OuterRef("pk"), status__in=OPEN_SUBSCRIPTION_STATUSES
    )
    evaluations = Evaluation.objects.filter(assessment__user=OuterRef("pk")).order_by(
        "-submitted_at"
    )
    any_treatment = Treatment.objects.filter(patient=OuterRef("pk"))
    any_approval = Evaluation.objects.filter(
        assessment__user=OuterRef("pk"), status=EvaluationStatus.APPROVED
    )
    latest_order = Order.objects.filter(user=OuterRef("pk")).order_by("-placed_at")
    status = Case(
        When(Q(suspended_at__isnull=False) | Q(is_active=False), then=Value("SUSPENDED")),
        When(Q(Exists(active_treatment)) | Q(Exists(open_subscription)), then=Value("ACTIVE")),
        When(
            latest_evaluation_status__in=(
                EvaluationStatus.NOT_SUITABLE,
                EvaluationStatus.SCREENED_OUT,
            ),
            then=Value("NOT_SUITABLE"),
        ),
        When(Q(Exists(any_treatment)) | Q(Exists(any_approval)), then=Value("INACTIVE")),
        default=Value("PENDING"),
        output_field=CharField(),
    )
    queryset: QuerySet[User] = (
        User.objects.filter(role=Role.PATIENT)
        .select_related("patient_profile__assigned_physician__clinician_profile")
        .annotate(latest_evaluation_status=Subquery(evaluations.values("status")[:1]))
        .annotate(
            admin_status=status,
            last_activity_at=Greatest(
                "date_joined",
                "last_login",
                Subquery(evaluations.values("submitted_at")[:1]),
                Subquery(latest_order.values("placed_at")[:1]),
            ),
        )
    )
    return queryset


def filtered(params: dict[str, Any], *, skip_status: bool = False) -> QuerySet[User]:
    queryset = annotated()
    if params.get("search"):
        term = params["search"]
        queryset = queryset.filter(Q(full_name__icontains=term) | Q(email__icontains=term))
    if params.get("status") and not skip_status:
        queryset = queryset.filter(**{"admin_status": params["status"]})
    if params.get("physician_id"):
        physician_id = params["physician_id"]
        queryset = queryset.filter(
            Q(patient_profile__assigned_physician_id=physician_id)
            | Q(treatments__prescribing_physician_id=physician_id)
            | Q(assessments__evaluation__physician_id=physician_id)
        ).distinct()
    if params.get("program_id"):
        queryset = queryset.filter(assessments__program_id=params["program_id"]).distinct()
    ordering = {
        "-last_activity_at": ("-last_activity_at", "full_name"),
        "name": ("full_name", "email"),
        "-created_at": ("-date_joined",),
    }[params.get("ordering") or "-last_activity_at"]
    return queryset.order_by(*ordering)


def summary(params: dict[str, Any]) -> dict[str, int]:
    rows = (
        filtered(params, skip_status=True)
        .order_by()
        .values("admin_status")  # type: ignore[misc]
        .annotate(n=Count("id", distinct=True))
    )
    counts = {row["admin_status"]: row["n"] for row in rows}
    return {
        "total": sum(counts.values()),
        "pending": counts.get("PENDING", 0),
        "active": counts.get("ACTIVE", 0),
        "not_suitable": counts.get("NOT_SUITABLE", 0),
        "suspended": counts.get("SUSPENDED", 0),
    }


def get_patient(patient_id: Any) -> User:
    patient = annotated().filter(pk=patient_id).first()
    if patient is None:
        raise NotFound()
    return patient


def care_physician(patient: User) -> User | None:
    profile = get_or_create_profile(patient)
    if profile.assigned_physician is not None:
        return profile.assigned_physician
    treatment = (
        Treatment.objects.filter(patient=patient)
        .select_related("prescribing_physician")
        .order_by("-started_at")
        .first()
    )
    if treatment is not None:
        return treatment.prescribing_physician
    evaluation = (
        Evaluation.objects.filter(assessment__user=patient, physician__isnull=False)
        .select_related("physician")
        .order_by("-submitted_at")
        .first()
    )
    return evaluation.physician if evaluation else None


def _active_treatment(patient: User) -> Treatment | None:
    return (
        Treatment.objects.filter(patient=patient, status=TreatmentStatus.ACTIVE)
        .select_related("program", "prescribing_physician")
        .order_by("-started_at")
        .first()
    )


def _open_subscription(patient: User) -> Subscription | None:
    return (
        Subscription.objects.filter(user=patient, status__in=OPEN_SUBSCRIPTION_STATUSES)
        .select_related("package")
        .order_by("-created_at")
        .first()
    )


def row_payload(patient: User, *, viewer: User) -> dict[str, Any]:
    status = getattr(patient, "admin_status", None) or "PENDING"
    treatment = _active_treatment(patient)
    subscription = _open_subscription(patient)
    plan_label = None
    if treatment is not None:
        plan_label = treatment.medication_name
        if subscription is not None:
            months = max(1, round(subscription.package.supply_duration_weeks / 4))
            plan_label = f"{plan_label} {months}-Mo"
    renews_on = None
    if subscription is not None and subscription.next_billing_at is not None:
        renews_on = timezone.localdate(subscription.next_billing_at)
    physician = care_physician(patient)
    return {
        "id": patient.pk,
        "name": patient.get_full_name(),
        "email": patient.email,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "plan_label": plan_label,
        "renews_on": renews_on,
        "last_activity_at": getattr(patient, "last_activity_at", None) or patient.date_joined,
        "joined_at": patient.date_joined,
        "assigned_physician": physician_summary(physician) if physician else None,
        "can_assign_physician": status != "SUSPENDED"
        and has_perm(viewer, Permission.EVALUATIONS_ASSIGN),
    }


# ---------------------------------------------------------------------------
# Detail (4.2)
# ---------------------------------------------------------------------------


def _plan(patient: User) -> dict[str, Any] | None:
    treatment = _active_treatment(patient)
    if treatment is None:
        return None
    subscription = _open_subscription(patient)
    total = treatment.duration_weeks or (
        subscription.package.supply_duration_weeks if subscription else None
    )
    total = int(total or 12)
    elapsed = (timezone.localdate() - treatment.started_at).days // 7 + 1
    return {
        "protocol_name": treatment.title,
        "program": {"id": treatment.program_id, "name": treatment.program.name},
        "current_week": max(1, min(total, elapsed)),
        "total_weeks": total,
        "phase_label": None,
        "subscription_id": subscription.pk if subscription else None,
    }


def _timeline(patient: User) -> list[dict[str, Any]]:
    from apps.support.models import SupportTicket

    events: list[dict[str, Any]] = []
    for entry in (
        Evaluation.objects.filter(assessment__user=patient)
        .prefetch_related("status_history__actor")
        .order_by("-submitted_at")[:10]
    ):
        for history in entry.status_history.all():
            events.append(
                {
                    "type": "EVALUATION",
                    "title": f"Evaluation {history.to_status.replace('_', ' ').lower()}",
                    "at": history.created_at,
                    "actor_name": history.actor.get_full_name() if history.actor else None,
                }
            )
    for order_history in (
        OrderStatusHistory.objects.filter(order__user=patient)
        .select_related("actor", "order")
        .order_by("-created_at")[:20]
    ):
        events.append(
            {
                "type": "ORDER",
                "title": f"Order {order_history.order.number} "
                f"{order_history.to_status.replace('_', ' ').lower()}",
                "at": order_history.created_at,
                "actor_name": (
                    order_history.actor.get_full_name() if order_history.actor else None
                ),
            }
        )
    for consultation in Consultation.objects.filter(patient=patient).order_by("-start")[:10]:
        events.append(
            {
                "type": "CONSULTATION",
                "title": f"Consultation {consultation.status.replace('_', ' ').lower()}",
                "at": consultation.updated_at,
                "actor_name": None,
            }
        )
    for ticket in SupportTicket.objects.filter(patient=patient).order_by("-created_at")[:10]:
        events.append(
            {
                "type": "SUPPORT_TICKET",
                "title": f"Support ticket {ticket.reference} opened",
                "at": ticket.created_at,
                "actor_name": None,
            }
        )
    events.sort(key=lambda item: item["at"], reverse=True)
    return events[:20]


def _clinical(patient: User) -> dict[str, Any] | None:
    treatment = _active_treatment(patient)
    treatments = Treatment.objects.filter(patient=patient).order_by("-started_at")
    note = ClinicalNote.objects.filter(patient=patient, kind=NoteKind.CLINICAL).first()
    prescriber = treatment.prescribing_physician if treatment else None
    return {
        "administration_route": treatment.route if treatment else None,
        "refills": (
            {"remaining": treatment.refills_remaining, "total": treatment.refills_total}
            if treatment is not None and treatment.has_refills
            else None
        ),
        "prescribing_physician": physician_summary(prescriber) if prescriber else None,
        "clinical_notes_summary": note.body[:280] if note else None,
        "treatments": [
            {
                "id": item.pk,
                "title": item.title,
                "medication_name": item.medication_name,
                "dose_label": item.dose_label,
                "schedule_label": item.schedule_label,
                "status": item.status,
                "started_at": item.started_at,
                "instructions": item.instructions or None,
            }
            for item in treatments
        ],
    }


def detail_payload(patient: User, *, viewer: User, request: Any) -> dict[str, Any]:
    row = row_payload(patient, viewer=viewer)
    measurement: AssessmentMeasurement | None = latest_measurement(patient)
    profile = get_or_create_profile(patient)
    address = profile.default_address
    clinical_access = has_perm(viewer, Permission.CLINICAL_READ)
    dob: date | None = measurement.date_of_birth if measurement else None
    body = {
        "id": patient.pk,
        "status": row["status"],
        "status_label": row["status_label"],
        "personal": {
            "name": patient.get_full_name(),
            "email": patient.email,
            "phone": profile.phone or None,
            "age_years": age_on(dob) if dob else None,
            "sex": measurement.sex if measurement else None,
            "date_of_birth": dob,
            "shipping_address": AddressSerializer(address).data if address else None,
        },
        "assigned_physician": row["assigned_physician"],
        "plan": _plan(patient),
        "stats": [
            {
                "key": "orders_count",
                "label": "Orders",
                "value": str(Order.objects.filter(user=patient).count()),
            },
            {
                "key": "consultations_count",
                "label": "Consultations",
                "value": str(Consultation.objects.filter(patient=patient).count()),
            },
            {
                "key": "evaluations_count",
                "label": "Evaluations",
                "value": str(Evaluation.objects.filter(assessment__user=patient).count()),
            },
            {
                "key": "last_login",
                "label": "Last login",
                "value": (
                    timezone.localtime(patient.last_login).strftime("%Y-%m-%d %H:%M")
                    if patient.last_login
                    else "Never"
                ),
            },
        ],
        "timeline": _timeline(patient),
        "clinical": _clinical(patient) if clinical_access else None,
    }
    record_audit(
        actor=viewer,
        action="patient.viewed",
        object_type="User",
        object_id=patient.pk,
        request=request,
        patient_id=patient.pk,
        metadata={"clinical": clinical_access},
    )
    return body


# ---------------------------------------------------------------------------
# Actions (4.3)
# ---------------------------------------------------------------------------


def assign_physician(
    *, patient: User, physician_id: Any, reason: str, viewer: User, request: Any
) -> User:
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    physician = eligible_physician(physician_id)
    with transaction.atomic():
        profile = PatientProfile.objects.select_for_update().get(
            pk=get_or_create_profile(patient).pk
        )
        previous = profile.assigned_physician_id
        profile.assigned_physician = physician
        profile.save(update_fields=["assigned_physician", "updated_at"])
        notify(
            physician,
            NotificationType.PATIENT_ASSIGNED,
            target_kind="clinical_patient",
            target_id=patient.pk,
            email=False,
        )
        record_audit(
            actor=viewer,
            action="patient.physician_assigned",
            object_type="User",
            object_id=patient.pk,
            request=request,
            patient_id=patient.pk,
            changed_fields=["assigned_physician"],
            metadata={
                "from": str(previous) if previous else None,
                "to": str(physician.pk),
                "reason": strip_tags(reason or "").strip(),
            },
        )
    return get_patient(patient.pk)


def suspend(*, patient: User, reason: str, viewer: User, request: Any) -> User:
    from apps.core.exceptions import InvalidStateTransition

    if patient.suspended_at is not None or not patient.is_active:
        raise InvalidStateTransition()
    with transaction.atomic():
        suspend_user(patient, reason=reason)
        for subscription in Subscription.objects.select_for_update().filter(
            user=patient, status=SubscriptionStatus.ACTIVE
        ):
            subscription.status = SubscriptionStatus.PAUSED
            subscription.paused_until = None
            subscription.next_billing_at = None
            subscription.pause_reason = "Account suspended"
            subscription.save(
                update_fields=[
                    "status",
                    "paused_until",
                    "next_billing_at",
                    "pause_reason",
                    "updated_at",
                ]
            )
        record_audit(
            actor=viewer,
            action="patient.suspended",
            object_type="User",
            object_id=patient.pk,
            request=request,
            patient_id=patient.pk,
        )
    return get_patient(patient.pk)


def reactivate(*, patient: User, viewer: User, request: Any) -> User:
    from apps.core.exceptions import InvalidStateTransition

    if patient.suspended_at is None and patient.is_active:
        raise InvalidStateTransition()
    with transaction.atomic():
        reactivate_user(patient)
        record_audit(
            actor=viewer,
            action="patient.reactivated",
            object_type="User",
            object_id=patient.pk,
            request=request,
            patient_id=patient.pk,
        )
    return get_patient(patient.pk)
