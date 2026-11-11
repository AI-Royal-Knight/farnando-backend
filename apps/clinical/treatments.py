"""My Patients, chart, notes, treatment management and renewals (Part 4, section 5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.enums import EvaluationStatus
from apps.assessments.models import Evaluation
from apps.clinical.evaluations import dose_in, dose_label, patient_brief
from apps.clinical.models import ClinicalNote
from apps.consultations.clinicians import physician_summary
from apps.consultations.models import Consultation
from apps.core.audit import record_audit
from apps.core.exceptions import DoseOutOfRange, InvalidStateTransition
from apps.messaging.models import Message
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.orders.models import OPEN_SUBSCRIPTION_STATUSES, Order, OrderStatus, Subscription
from apps.treatments.models import (
    RenewalRequestStatus,
    Treatment,
    TreatmentRenewalRequest,
    TreatmentStatus,
    TreatmentVersion,
)
from apps.treatments.services import next_dose_due

LIST_STATUS: dict[str, str] = {
    TreatmentStatus.ACTIVE: "ACTIVE_TREATMENT",
    TreatmentStatus.PAUSED: "PAUSED",
    TreatmentStatus.COMPLETED: "COMPLETED",
    TreatmentStatus.DISCONTINUED: "DISCONTINUED",
}


# ---------------------------------------------------------------------------
# Care relationship
# ---------------------------------------------------------------------------


def care_patients(physician: User) -> QuerySet[User]:
    return (
        User.objects.filter(role=Role.PATIENT)
        .filter(
            Q(assessments__evaluation__physician=physician)
            | Q(treatments__prescribing_physician=physician)
            | Q(consultations__physician=physician)
        )
        .distinct()
    )


def has_care_relationship(physician: User, patient_id: Any) -> bool:
    return care_patients(physician).filter(pk=patient_id).exists()


def is_treating(physician: User, patient: User) -> bool:
    return (
        Treatment.objects.filter(patient=patient, prescribing_physician=physician).exists()
        or Evaluation.objects.filter(assessment__user=patient, physician=physician).exists()
    )


def get_care_patient(user: User, patient_id: Any) -> User:
    queryset = (
        User.objects.filter(role=Role.PATIENT) if user.role == Role.ADMIN else care_patients(user)
    )
    patient = queryset.filter(pk=patient_id).first()
    if patient is None:
        raise NotFound()
    return patient


# ---------------------------------------------------------------------------
# My Patients
# ---------------------------------------------------------------------------


def _patient_status(physician: User, patient: User, treatment: Treatment | None) -> str | None:
    cutoff = timezone.now() - timedelta(days=settings.NEWLY_APPROVED_DAYS)
    newly = Evaluation.objects.filter(
        assessment__user=patient,
        physician=physician,
        status=EvaluationStatus.APPROVED,
        decided_at__gte=cutoff,
    ).exists()
    if newly and not Order.objects.filter(user=patient, status=OrderStatus.DELIVERED).exists():
        return "NEWLY_APPROVED"
    if treatment is not None:
        return LIST_STATUS.get(treatment.status)
    return None


def _last_activity(physician: User, patient: User) -> datetime | None:
    candidates = [
        Evaluation.objects.filter(assessment__user=patient).aggregate(v=Max("updated_at"))["v"],
        Treatment.objects.filter(patient=patient).aggregate(v=Max("updated_at"))["v"],
        Consultation.objects.filter(patient=patient, physician=physician).aggregate(v=Max("start"))[
            "v"
        ],
        Message.objects.filter(thread__patient=patient, thread__participant=physician).aggregate(
            v=Max("created_at")
        )["v"],
    ]
    values = [value for value in candidates if value is not None]
    return max(values) if values else None


def patient_row(physician: User, patient: User) -> dict[str, Any]:
    treatment = (
        Treatment.objects.filter(patient=patient)
        .select_related("program")
        .order_by("-started_at", "-created_at")
        .first()
    )
    program: dict[str, Any] | None
    if treatment is not None:
        program = {"id": treatment.program_id, "name": treatment.program.name}
        medication = f"{treatment.medication_name} {treatment.strength_label}".strip()
        if treatment.status not in (TreatmentStatus.ACTIVE, TreatmentStatus.PAUSED):
            medication = ""
    else:
        evaluation = (
            Evaluation.objects.filter(assessment__user=patient)
            .select_related("assessment__program")
            .order_by("-submitted_at")
            .first()
        )
        program = (
            {
                "id": evaluation.assessment.program_id,
                "name": evaluation.assessment.program.name,
            }
            if evaluation
            else None
        )
        medication = ""
    return {
        "patient": patient_brief(patient),
        "program": program,
        "medication_label": medication,
        "status": _patient_status(physician, patient, treatment),
        "last_activity_at": _last_activity(physician, patient),
    }


def list_patients(
    physician: User,
    *,
    statuses: list[str],
    search: str,
    program_id: Any,
    ordering: str,
) -> list[dict[str, Any]]:
    queryset = (
        User.objects.filter(role=Role.PATIENT, assessments__isnull=False).distinct()
        if physician.role == Role.ADMIN
        else care_patients(physician)
    )
    if search:
        queryset = queryset.filter(full_name__icontains=search)
    if program_id:
        queryset = queryset.filter(
            Q(treatments__program_id=program_id) | Q(assessments__program_id=program_id)
        ).distinct()
    rows = [patient_row(physician, patient) for patient in queryset]
    if statuses:
        rows = [row for row in rows if row["status"] in statuses]
    if ordering == "-last_activity_at":
        epoch = timezone.make_aware(datetime(1970, 1, 1))
        rows.sort(key=lambda row: row["last_activity_at"] or epoch, reverse=True)
    else:
        rows.sort(key=lambda row: str(row["patient"]["name"]).casefold())
    return rows


# ---------------------------------------------------------------------------
# Treatments
# ---------------------------------------------------------------------------


def treatment_payload(treatment: Treatment) -> dict[str, Any]:
    versions = treatment.versions.select_related("changed_by__clinician_profile").order_by(
        "version"
    )
    return {
        "id": treatment.pk,
        "status": treatment.status,
        "patient": patient_brief(treatment.patient),
        "program": {"id": treatment.program_id, "name": treatment.program.name},
        "product": (
            {"id": treatment.product_id, "name": treatment.product.name}
            if treatment.product
            else None
        ),
        "current_dose": {
            "value": float(treatment.dose_value),
            "unit": treatment.dose_unit,
            "label": treatment.dose_label
            or f"{float(treatment.dose_value):g} {treatment.dose_unit}",
        },
        "schedule": {
            "frequency": treatment.frequency,
            "day_of_week": treatment.day_of_week,
            "time_of_day": treatment.time_of_day,
        },
        "refills": {
            "remaining": int(treatment.refills_remaining or 0),
            "total": int(treatment.refills_total or 0),
        },
        "next_dose_due_at": next_dose_due(treatment),
        "started_at": treatment.started_at,
        "valid_until": treatment.prescription_expires_at,
        "versions": [
            {
                "version": version.version,
                "dose": {
                    "value": float(version.dose_value),
                    "unit": version.dose_unit,
                    "label": version.dose_label,
                },
                "effective_from": version.effective_from,
                "reason": version.reason,
                "changed_by": physician_summary(version.changed_by),
                "changed_at": version.created_at,
            }
            for version in versions
        ],
    }


def get_treatment(physician: User, treatment_id: Any, *, lock: bool = False) -> Treatment:
    queryset = Treatment.objects.select_related("program", "product", "patient")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    treatment = queryset.filter(pk=treatment_id).first()
    if treatment is None:
        raise NotFound()
    if treatment.prescribing_physician_id != physician.pk and not has_care_relationship(
        physician, treatment.patient_id
    ):
        raise NotFound()
    return treatment


def _notify_patient(treatment: Treatment) -> None:
    notify(
        treatment.patient,
        NotificationType.TREATMENT_UPDATED,
        target_kind="treatment",
        target_id=treatment.pk,
    )


def apply_version(treatment: Treatment, version: TreatmentVersion) -> None:
    treatment.dose_value = version.dose_value
    treatment.dose_unit = version.dose_unit
    treatment.dose_label = version.dose_label
    treatment.frequency = version.frequency
    treatment.day_of_week = version.day_of_week
    treatment.time_of_day = version.time_of_day
    if version.instructions:
        treatment.instructions = version.instructions
    treatment.save(
        update_fields=[
            "dose_value",
            "dose_unit",
            "dose_label",
            "frequency",
            "day_of_week",
            "time_of_day",
            "instructions",
            "updated_at",
        ]
    )
    version.applied_at = timezone.now()
    version.save(update_fields=["applied_at", "updated_at"])


def apply_due_versions(today: date | None = None) -> int:
    today = today or timezone.localdate()
    count = 0
    due = TreatmentVersion.objects.filter(
        applied_at__isnull=True, effective_from__lte=today
    ).order_by("treatment_id", "version")
    for version in due:
        with transaction.atomic():
            treatment = Treatment.objects.select_for_update().get(pk=version.treatment_id)
            apply_version(treatment, version)
            _notify_patient(treatment)
        count += 1
    return count


def adjust(*, physician: User, treatment_id: Any, data: dict[str, Any], request: Any) -> Treatment:
    today = timezone.localdate()
    effective = data.get("effective_from") or today
    if not today <= effective <= today + timedelta(days=14):
        raise ValidationError({"effective_from": ["Must be today to 14 days ahead."]})
    with transaction.atomic():
        treatment = get_treatment(physician, treatment_id, lock=True)
        if treatment.status not in (TreatmentStatus.ACTIVE, TreatmentStatus.PAUSED):
            raise InvalidStateTransition()
        if treatment.product is None:
            raise DoseOutOfRange()
        option = dose_in(data["dose"], treatment.product.allowed_doses)
        if option is None:
            raise DoseOutOfRange()
        frequency = data.get("frequency") or treatment.frequency
        last = treatment.versions.order_by("-version").first()
        version = TreatmentVersion.objects.create(
            treatment=treatment,
            version=(last.version + 1) if last else 1,
            dose_value=Decimal(str(data["dose"]["value"])),
            dose_unit=data["dose"]["unit"],
            dose_label=dose_label(data["dose"], option),
            frequency=frequency,
            day_of_week=data.get("day_of_week", treatment.day_of_week),
            time_of_day=data.get("time_of_day") or treatment.time_of_day,
            instructions=strip_tags(data.get("instructions") or "").strip(),
            effective_from=effective,
            reason=strip_tags(data["reason"]).strip(),
            patient_note=strip_tags(data.get("patient_note") or "").strip(),
            changed_by=physician,
        )
        if effective <= today:
            apply_version(treatment, version)
        _notify_patient(treatment)
        record_audit(
            actor=physician,
            action="treatment.adjusted",
            object_type="Treatment",
            object_id=treatment.pk,
            request=request,
            metadata={"version": version.version},
        )
    return treatment


STATUS_ACTIONS = {
    "pause": ({TreatmentStatus.ACTIVE}, TreatmentStatus.PAUSED),
    "resume": ({TreatmentStatus.PAUSED}, TreatmentStatus.ACTIVE),
    "discontinue": (
        {TreatmentStatus.ACTIVE, TreatmentStatus.PAUSED},
        TreatmentStatus.DISCONTINUED,
    ),
}


def change_status(
    *, physician: User, treatment_id: Any, action: str, data: dict[str, Any], request: Any
) -> Treatment:
    allowed, target = STATUS_ACTIONS[action]
    with transaction.atomic():
        treatment = get_treatment(physician, treatment_id, lock=True)
        if treatment.status not in allowed:
            raise InvalidStateTransition()
        from_status = treatment.status
        treatment.status = target
        treatment.save(update_fields=["status", "updated_at"])
        if target == TreatmentStatus.DISCONTINUED:
            _discontinue_side_effects(treatment, physician, request)
        _notify_patient(treatment)
        record_audit(
            actor=physician,
            action=f"treatment.{action}",
            object_type="Treatment",
            object_id=treatment.pk,
            request=request,
            metadata={"from": from_status, "to": target},
        )
    return treatment


def _discontinue_side_effects(treatment: Treatment, physician: User, request: Any) -> None:
    from apps.orders.services import PATIENT_CANCELLABLE, cancel_order

    for order in Order.objects.filter(treatment=treatment, status__in=PATIENT_CANCELLABLE):
        cancel_order(order=order, reason="Treatment discontinued", actor=physician, request=request)
    flagged = Subscription.objects.filter(
        treatment=treatment, status__in=OPEN_SUBSCRIPTION_STATUSES
    ).update(
        needs_staff_review=True,
        staff_review_reason="Treatment discontinued by physician",
        updated_at=timezone.now(),
    )
    if flagged:
        from apps.clinical.tasks import send_staff_alert

        transaction.on_commit(lambda: send_staff_alert.delay("subscription_needs_review"))


# ---------------------------------------------------------------------------
# Renewal requests
# ---------------------------------------------------------------------------


def renewals_for(physician: User) -> QuerySet[TreatmentRenewalRequest]:
    return (
        TreatmentRenewalRequest.objects.select_related(
            "treatment__patient", "treatment__program", "treatment__product"
        )
        .filter(
            Q(treatment__prescribing_physician=physician)
            | Q(treatment__patient__in=care_patients(physician))
        )
        .distinct()
        .order_by("-created_at")
    )


def renewal_payload(renewal: TreatmentRenewalRequest) -> dict[str, Any]:
    treatment = renewal.treatment
    summary = treatment_payload(treatment)
    summary.pop("versions", None)
    return {
        "id": renewal.pk,
        "status": renewal.status,
        "patient": patient_brief(treatment.patient),
        "treatment": summary,
        "patient_note": renewal.note or None,
        "created_at": renewal.created_at,
    }


def _get_renewal(physician: User, renewal_id: Any) -> TreatmentRenewalRequest:
    renewal = renewals_for(physician).filter(pk=renewal_id).first()
    if renewal is None:
        raise NotFound()
    if renewal.status != RenewalRequestStatus.REQUESTED:
        raise InvalidStateTransition()
    return renewal


def approve_renewal(
    *, physician: User, renewal_id: Any, data: dict[str, Any], request: Any
) -> TreatmentRenewalRequest:
    with transaction.atomic():
        renewal = _get_renewal(physician, renewal_id)
        treatment = (
            Treatment.objects.select_for_update(of=("self",))
            .select_related("product")
            .get(pk=renewal.treatment_id)
        )
        max_refills = treatment.product.max_refills if treatment.product else 0
        added = int(data["refills_added"])
        if not 1 <= added <= max(max_refills, 1):
            raise ValidationError({"refills_added": [f"Must be 1 to {max_refills}."]})
        valid_until = data.get("valid_until")
        today = timezone.localdate()
        if valid_until is not None and not today < valid_until <= today + timedelta(days=366):
            raise ValidationError({"valid_until": ["Must be after today, at most 12 months."]})
        treatment.refills_total = (treatment.refills_total or 0) + added
        treatment.refills_remaining = (treatment.refills_remaining or 0) + added
        if valid_until is not None:
            treatment.prescription_expires_at = valid_until
        treatment.save(
            update_fields=[
                "refills_total",
                "refills_remaining",
                "prescription_expires_at",
                "updated_at",
            ]
        )
        renewal.status = RenewalRequestStatus.APPROVED
        renewal.resolved_at = timezone.now()
        renewal.resolved_by = physician
        renewal.patient_message = strip_tags(data.get("note") or "").strip()
        renewal.save(
            update_fields=["status", "resolved_at", "resolved_by", "patient_message", "updated_at"]
        )
        _notify_patient(treatment)
        record_audit(
            actor=physician,
            action="renewal.approved",
            object_type="TreatmentRenewalRequest",
            object_id=renewal.pk,
            request=request,
            metadata={"refills_added": added},
        )
    return renewal


def deny_renewal(
    *, physician: User, renewal_id: Any, data: dict[str, Any], request: Any
) -> TreatmentRenewalRequest:
    with transaction.atomic():
        renewal = _get_renewal(physician, renewal_id)
        renewal.status = RenewalRequestStatus.DENIED
        renewal.resolved_at = timezone.now()
        renewal.resolved_by = physician
        renewal.decision_note = strip_tags(data["reason"]).strip()
        renewal.patient_message = strip_tags(data.get("patient_note") or "").strip()
        renewal.save(
            update_fields=[
                "status",
                "resolved_at",
                "resolved_by",
                "decision_note",
                "patient_message",
                "updated_at",
            ]
        )
        _notify_patient(renewal.treatment)
        record_audit(
            actor=physician,
            action="renewal.denied",
            object_type="TreatmentRenewalRequest",
            object_id=renewal.pk,
            request=request,
        )
    return renewal


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def note_payload(note: ClinicalNote) -> dict[str, Any]:
    return {
        "id": note.pk,
        "kind": note.kind,
        "body": note.body,
        "author": physician_summary(note.author),
        "created_at": note.created_at,
        "consultation_id": note.consultation_id,
    }


def add_note(
    *, author: User, patient: User, kind: str, body: str, consultation_id: Any = None
) -> ClinicalNote:
    consultation = None
    if consultation_id:
        consultation = Consultation.objects.filter(
            pk=consultation_id, patient=patient, physician=author
        ).first()
        if consultation is None:
            raise ValidationError({"consultation_id": ["Unknown consultation."]})
    return ClinicalNote.objects.create(
        patient=patient,
        author=author,
        kind=kind,
        body=strip_tags(body).strip(),
        consultation=consultation,
    )


__all__ = [
    "add_note",
    "adjust",
    "apply_due_versions",
    "approve_renewal",
    "care_patients",
    "change_status",
    "deny_renewal",
    "get_care_patient",
    "get_treatment",
    "has_care_relationship",
    "is_treating",
    "list_patients",
    "note_payload",
    "patient_row",
    "renewal_payload",
    "renewals_for",
    "treatment_payload",
]
