"""Dashboard overview (Part 3 section 3) and sidebar visibility (section 0)."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.enums import AssessmentStatus, EvaluationStatus, LabsStatus
from apps.assessments.models import Assessment, AssessmentMeasurement, Evaluation
from apps.assessments.services import (
    current_step_key,
    display_weight,
    journey_for,
    projection_for,
    screening_message,
)
from apps.consultations.models import OPEN_CONSULTATION_STATUSES, Consultation
from apps.consultations.services import ELIGIBLE_EVALUATION_STATUSES
from apps.messaging.models import MessageThread
from apps.messaging.services import patient_unread_total
from apps.notifications.services import unread_count
from apps.orders.models import INACTIVE_ORDER_STATUSES, Order
from apps.patients.services import care_team_users, relevant_assessment
from apps.treatments.models import Treatment, TreatmentStatus
from apps.treatments.services import next_dose_due, patient_timezone

ACTION_LABELS = {
    "RESUME_ASSESSMENT": "Resume assessment",
    "PAY_FEE": "Pay evaluation fee",
    "UPLOAD_LABS": "Upload lab results",
    "SET_APPOINTMENT": "Set appointment",
    "ORDER_MEDICINE": "Order medicine",
    "VIEW_TREATMENT": "View treatment",
    "NONE": "",
}

OPEN_EVALUATION_STATUSES = (
    EvaluationStatus.SUBMITTED,
    EvaluationStatus.IN_REVIEW,
    EvaluationStatus.PENDING_LABS,
)


def _evaluation(assessment: Assessment | None) -> Evaluation | None:
    if assessment is None:
        return None
    return Evaluation.objects.filter(assessment=assessment).first()


def sections_for(user: User) -> list[dict[str, Any]]:
    evaluations = Evaluation.objects.filter(assessment__user=user)
    consultations = evaluations.filter(status__in=ELIGIBLE_EVALUATION_STATUSES).exists()
    approved = evaluations.filter(status=EvaluationStatus.APPROVED).exists()
    has_order = Order.objects.filter(user=user).exists()
    messages = bool(care_team_users(user)) or MessageThread.objects.filter(patient=user).exists()
    return [
        {"key": "OVERVIEW", "enabled": True},
        {"key": "ASSESSMENT", "enabled": True},
        {"key": "CONSULTATIONS", "enabled": consultations},
        {"key": "TREATMENT", "enabled": approved},
        {"key": "ORDERS", "enabled": approved or has_order},
        {"key": "MESSAGES", "enabled": messages},
    ]


def _assessment_payload(
    assessment: Assessment | None, evaluation: Evaluation | None
) -> dict[str, Any] | None:
    if assessment is None:
        return None
    return {
        "id": assessment.pk,
        "program_name": assessment.program.name,
        "status": assessment.status,
        "evaluation_status": evaluation.status if evaluation is not None else None,
        "journey": journey_for(assessment),
        "current_step_key": (
            current_step_key(assessment) if assessment.status == AssessmentStatus.DRAFT else None
        ),
    }


def _health_summary(assessment: Assessment | None) -> dict[str, Any] | None:
    if assessment is None:
        return None
    measurement = AssessmentMeasurement.objects.filter(assessment=assessment).first()
    if measurement is None:
        return None
    program = assessment.program
    projection, weeks = projection_for(program, measurement)
    try:
        subheadline = program.health_summary_subheadline.format(
            months=program.projection_months, weeks=weeks
        )
    except (KeyError, IndexError, ValueError):
        subheadline = program.health_summary_subheadline
    unit = assessment.display_weight_unit
    return {
        "headline": screening_message(program, assessment.screening_result or "ELIGIBLE"),
        "subheadline": subheadline,
        "bmi": float(measurement.bmi),
        "bmi_category": measurement.bmi_category,
        "current_weight": display_weight(measurement.current_weight_kg, unit),
        "target_weight": display_weight(measurement.target_weight_kg, unit),
        "projection": projection,
        "disclaimer": program.projection_disclaimer,
    }


def _primary_action(
    user: User, assessment: Assessment | None, evaluation: Evaluation | None
) -> dict[str, Any]:
    def action(kind: str, target: Any = None) -> dict[str, Any]:
        return {"type": kind, "label": ACTION_LABELS[kind], "target_id": target}

    if assessment is None:
        return action("NONE")
    status = evaluation.status if evaluation is not None else None
    if status == EvaluationStatus.AWAITING_PAYMENT:
        return action("PAY_FEE", assessment.pk)
    if assessment.status == AssessmentStatus.DRAFT:
        return action("RESUME_ASSESSMENT", assessment.pk)
    if status == EvaluationStatus.PENDING_LABS or (
        status in OPEN_EVALUATION_STATUSES
        and assessment.labs_status in (LabsStatus.NOT_STARTED, LabsStatus.DEFERRED)
    ):
        return action("UPLOAD_LABS", assessment.pk)
    if status in (EvaluationStatus.SUBMITTED, EvaluationStatus.IN_REVIEW):
        booked = Consultation.objects.filter(
            patient=user, status__in=OPEN_CONSULTATION_STATUSES
        ).exists()
        if not booked:
            return action("SET_APPOINTMENT")
    if status == EvaluationStatus.APPROVED and evaluation is not None:
        treatment = (
            Treatment.objects.filter(patient=user, evaluation=evaluation)
            .order_by("-started_at")
            .first()
        )
        has_order = treatment is not None and Order.objects.filter(treatment=treatment).exists()
        target = treatment.pk if treatment is not None else assessment.pk
        return action("VIEW_TREATMENT" if has_order else "ORDER_MEDICINE", target)
    return action("NONE")


def _next_consultation(user: User) -> Consultation | None:
    return (
        Consultation.objects.select_related("consultation_type", "physician__clinician_profile")
        .filter(patient=user, status__in=OPEN_CONSULTATION_STATUSES, end__gte=timezone.now())
        .order_by("start")
        .first()
    )


def _next_dose(user: User) -> dict[str, Any] | None:
    tz_name = patient_timezone(user)
    best: tuple[Any, Treatment] | None = None
    for treatment in Treatment.objects.filter(patient=user, status=TreatmentStatus.ACTIVE):
        due = next_dose_due(treatment, tz_name=tz_name)
        if due is None:
            if best is None:
                best = (None, treatment)
            continue
        if best is None or best[0] is None or due < best[0]:
            best = (due, treatment)
    if best is None:
        return None
    due, treatment = best
    return {
        "treatment_id": treatment.pk,
        "medication_name": treatment.medication_name,
        "due_at": due,
        "time_of_day": treatment.time_of_day,
    }


def build_overview(user: User) -> dict[str, Any]:
    assessment = relevant_assessment(user)
    evaluation = _evaluation(assessment)
    return {
        "display_name": user.get_short_name() if user.full_name else "",
        "sections": sections_for(user),
        "assessment": _assessment_payload(assessment, evaluation),
        "health_summary": _health_summary(assessment),
        "primary_action": _primary_action(user, assessment, evaluation),
        "next_consultation": _next_consultation(user),
        "next_dose": _next_dose(user),
        "active_orders_count": Order.objects.filter(user=user)
        .exclude(status__in=INACTIVE_ORDER_STATUSES)
        .count(),
        "unread_messages_count": patient_unread_total(user),
        "unread_notifications_count": unread_count(user),
    }


__all__ = ["build_overview", "sections_for"]
