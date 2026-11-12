"""Staff views of evaluations: assignment, priority, labs and reopen (Part 5, section 5)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Case, CharField, Count, F, Q, QuerySet, Value, When
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.enums import EvaluationStatus, FeeStatus, LabsStatus
from apps.assessments.models import Evaluation, EvaluationFee, EvaluationReviewRequest
from apps.assessments.services import EvaluationService, lab_recommendations, money
from apps.backoffice.physicians import average_review_hours, eligible_physician
from apps.catalog import runtime
from apps.clinical import evaluations as clinical
from apps.consultations.clinicians import physician_summary
from apps.consultations.models import (
    AvailabilityBlock,
    AvailabilityKind,
    AvailabilityRule,
    ClinicianProfile,
    PhysicianVerificationStatus,
    TimeBlockKind,
)
from apps.core.audit import record_audit
from apps.core.exceptions import InvalidStateTransition
from apps.staff.models import Permission
from apps.staff.permissions import has_perm

STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "PENDING": (
        EvaluationStatus.SUBMITTED,
        EvaluationStatus.IN_REVIEW,
        EvaluationStatus.AWAITING_PAYMENT,
    ),
    "APPROVED": (EvaluationStatus.APPROVED,),
    "NOT_SUITABLE": (EvaluationStatus.NOT_SUITABLE,),
    "AWAITING_LABS": (EvaluationStatus.PENDING_LABS,),
    "SCREENED_OUT": (EvaluationStatus.SCREENED_OUT,),
    "CANCELLED": (EvaluationStatus.CANCELLED,),
}
OPEN_FOR_QUEUE = (EvaluationStatus.SUBMITTED, EvaluationStatus.IN_REVIEW)


def priority_expression() -> Case:
    cutoff = timezone.now() - timedelta(hours=runtime.hours("evaluation", "urgent_after_hours"))
    return Case(
        When(priority_set_by_staff=True, then=F("priority")),
        When(status__in=OPEN_FOR_QUEUE, submitted_at__lt=cutoff, then=Value("URGENT")),
        default=Value("NORMAL"),
        output_field=CharField(),
    )


def base() -> QuerySet[Evaluation]:
    return (
        clinical._base()
        .select_related("assigned_physician__clinician_profile", "fee")
        .annotate(effective_priority=priority_expression())
    )


def _local_bound(value: date, *, end: bool) -> datetime:
    moment = datetime.combine(value + timedelta(days=1) if end else value, time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


def filtered(params: dict[str, Any], *, skip_group: bool = False) -> QuerySet[Evaluation]:
    queryset = base()
    group = params.get("status_group")
    if group and not skip_group:
        queryset = queryset.filter(status__in=STATUS_GROUPS[group])
    if params.get("review_requested") is not None:
        exists = Q(review_request__isnull=False)
        queryset = queryset.filter(exists if params["review_requested"] else ~exists)
    assignment = params.get("assignment")
    if assignment == "UNASSIGNED":
        queryset = queryset.filter(physician__isnull=True, assigned_physician__isnull=True)
    elif assignment == "ASSIGNED":
        queryset = queryset.filter(Q(physician__isnull=False) | Q(assigned_physician__isnull=False))
    if params.get("priority"):
        queryset = queryset.filter(**{"effective_priority": params["priority"]})
    if params.get("labs"):
        queryset = clinical.filter_labs(queryset, params["labs"])
    if params.get("program_id"):
        queryset = queryset.filter(assessment__program_id=params["program_id"])
    if params.get("physician_id"):
        queryset = queryset.filter(
            Q(physician_id=params["physician_id"]) | Q(assigned_physician_id=params["physician_id"])
        )
    if params.get("search"):
        queryset = queryset.filter(assessment__user__full_name__icontains=params["search"])
    if params.get("from"):
        queryset = queryset.filter(submitted_at__gte=_local_bound(params["from"], end=False))
    if params.get("to"):
        queryset = queryset.filter(submitted_at__lt=_local_bound(params["to"], end=True))
    ordering = params.get("ordering") or "submitted_at"
    order_map = {
        "submitted_at": ("submitted_at",),
        "-submitted_at": ("-submitted_at",),
        "bmi": ("assessment__measurement__bmi", "submitted_at"),
    }
    return queryset.order_by(*order_map[ordering])


def counts(params: dict[str, Any]) -> dict[str, int]:
    queryset = filtered(params, skip_group=True)
    data = queryset.order_by().values("status").annotate(n=Count("id", distinct=True))
    by_status = {row["status"]: row["n"] for row in data}

    def total(group: str) -> int:
        return sum(by_status.get(status, 0) for status in STATUS_GROUPS[group])

    return {
        "pending": total("PENDING"),
        "approved": total("APPROVED"),
        "not_suitable": total("NOT_SUITABLE"),
        "awaiting_labs": total("AWAITING_LABS"),
    }


# ---------------------------------------------------------------------------
# Recommendations (5.3)
# ---------------------------------------------------------------------------


def _queue_sizes(user_ids: list[Any]) -> dict[Any, int]:
    sizes: dict[Any, int] = dict.fromkeys(user_ids, 0)
    open_statuses = (*OPEN_FOR_QUEUE, EvaluationStatus.PENDING_LABS)
    for row in (
        Evaluation.objects.filter(status__in=open_statuses, physician_id__in=user_ids)
        .values("physician_id")
        .annotate(n=Count("id"))
    ):
        sizes[row["physician_id"]] += row["n"]
    for row in (
        Evaluation.objects.filter(
            status__in=open_statuses, physician__isnull=True, assigned_physician_id__in=user_ids
        )
        .values("assigned_physician_id")
        .annotate(n=Count("id"))
    ):
        sizes[row["assigned_physician_id"]] += row["n"]
    return sizes


def _available_soon(user_ids: list[Any]) -> set[Any]:
    now = timezone.now()
    today = timezone.localdate()
    weekdays = {today.isoweekday(), (today + timedelta(days=1)).isoweekday()}
    available = set(
        AvailabilityRule.objects.filter(
            physician_id__in=user_ids, kind=AvailabilityKind.AVAILABLE, weekday__in=weekdays
        ).values_list("physician_id", flat=True)
    )
    available |= set(
        AvailabilityBlock.objects.filter(
            physician_id__in=user_ids,
            kind=TimeBlockKind.EXTRA_AVAILABLE,
            end__gt=now,
            start__lt=now + timedelta(hours=24),
        ).values_list("physician_id", flat=True)
    )
    return available


def recommendations(evaluation: Evaluation, *, limit: int = 5) -> list[dict[str, Any]]:
    program = evaluation.assessment.program
    allowed = list(program.allowed_specialties.values_list("pk", flat=True))
    profiles = ClinicianProfile.objects.filter(
        verification_status=PhysicianVerificationStatus.VERIFIED,
        accepts_new_consultations=True,
        user__role=Role.PHYSICIAN,
        user__is_active=True,
    ).select_related("user", "specialty_ref")
    if allowed:
        profiles = profiles.filter(specialty_ref_id__in=allowed)
    candidates = list(profiles)
    user_ids = [profile.user_id for profile in candidates]
    sizes = _queue_sizes(user_ids)
    soon = _available_soon(user_ids)
    scored: list[dict[str, Any]] = []
    for profile in candidates:
        reasons: list[str] = []
        score = 40 if allowed else 20
        if profile.specialty_ref is not None:
            reasons.append(profile.specialty_ref.name)
        size = sizes.get(profile.user_id, 0)
        score += max(0, 30 - 5 * size)
        reasons.append(f"{size} evaluation{'s' if size != 1 else ''} in queue")
        if profile.user_id in soon:
            score += 15
            reasons.append("Available today")
        hours = average_review_hours(profile.user)
        if hours is None:
            score += 5
        elif hours <= 24:
            score += 15
        elif hours <= 48:
            score += 8
        scored.append(
            {
                "physician": physician_summary(profile.user),
                "score": min(100, score),
                "reasons": reasons,
            }
        )
    scored.sort(key=lambda item: (-item["score"], item["physician"]["name"]))
    return scored[:limit]


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def item_payload(
    evaluation: Evaluation, *, recommended: dict[str, Any] | None = None
) -> dict[str, Any]:
    measurement = clinical.measurement_of(evaluation)
    assessment = evaluation.assessment
    patient = clinical.patient_brief(assessment.user, measurement)
    patient["avatar_url"] = None
    holder = evaluation.physician or evaluation.assigned_physician
    priority = getattr(evaluation, "effective_priority", None) or evaluation.priority
    return {
        "id": evaluation.pk,
        "status": evaluation.status,
        "submitted_at": evaluation.submitted_at,
        "patient": patient,
        "program": {"id": assessment.program_id, "name": assessment.program.name},
        "questionnaire_state": "COMPLETE",
        "program_eligibility": assessment.screening_result,
        "labs_state": clinical.labs_state(evaluation),
        "bmi": float(measurement.bmi) if measurement else None,
        "bmi_category": measurement.bmi_category if measurement else None,
        "priority": priority,
        "assigned_physician": physician_summary(holder) if holder else None,
        "recommended_physician": recommended,
    }


def list_payloads(evaluations: list[Evaluation]) -> list[dict[str, Any]]:
    cache: dict[Any, dict[str, Any] | None] = {}
    results = []
    for evaluation in evaluations:
        recommended = None
        unassigned = evaluation.physician_id is None and evaluation.assigned_physician_id is None
        if unassigned and evaluation.status in OPEN_FOR_QUEUE:
            program_id = evaluation.assessment.program_id
            if program_id not in cache:
                top = recommendations(evaluation, limit=1)
                cache[program_id] = top[0]["physician"] if top else None
            recommended = cache[program_id]
        results.append(item_payload(evaluation, recommended=recommended))
    return results


def get(evaluation_id: Any) -> Evaluation:
    evaluation = base().filter(pk=evaluation_id).first()
    if evaluation is None:
        raise NotFound()
    return evaluation


def detail_payload(evaluation: Evaluation, *, user: User, request: Any) -> dict[str, Any]:
    clinical_access = has_perm(user, Permission.CLINICAL_READ)
    body = list_payloads([evaluation])[0]
    history = evaluation.status_history.select_related("actor").order_by("created_at")
    fee = EvaluationFee.objects.filter(evaluation=evaluation).first()
    panels = lab_recommendations(evaluation)["panels"]
    body.update(
        {
            "version": evaluation.version,
            "status_history": [
                {
                    "from": entry.from_status or None,
                    "to": entry.to_status,
                    "at": entry.created_at,
                    "actor_name": entry.actor.get_full_name() if entry.actor else None,
                    "actor_role": entry.actor.role if entry.actor else None,
                }
                for entry in history
            ],
            "fee": (
                {"status": fee.status, "amount": money(fee.amount, fee.currency)} if fee else None
            ),
            "review_requested": EvaluationReviewRequest.objects.filter(
                evaluation=evaluation
            ).exists(),
            "lab_recommendations": [
                {"code": str(panel.get("code", "")), "name": str(panel.get("name", ""))}
                for panel in panels
                if isinstance(panel, dict)
            ],
        }
    )
    if clinical_access:
        measurement = clinical.measurement_of(evaluation)
        body["questionnaire"] = clinical.questionnaire_blocks(evaluation)
        body["biometrics"] = clinical.biometrics(measurement, "KG")
        body["attachments"] = clinical.lab_attachments([evaluation.assessment_id])
    record_audit(
        actor=user,
        action="evaluation.viewed",
        object_type="Evaluation",
        object_id=evaluation.pk,
        request=request,
        patient_id=evaluation.assessment.user_id,
        metadata={"clinical": clinical_access},
    )
    return body


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _locked(evaluation_id: Any, version: int | None) -> Evaluation:
    evaluation = clinical._locked(evaluation_id)
    if version is not None:
        clinical.check_version(evaluation, version)
    return evaluation


def assign(
    *, evaluation_id: Any, physician_id: Any, note: str, version: int, user: User, request: Any
) -> Evaluation:
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    with transaction.atomic():
        evaluation = _locked(evaluation_id, version)
        if evaluation.status not in OPEN_FOR_QUEUE:
            raise InvalidStateTransition()
        physician = eligible_physician(physician_id, evaluation.assessment.program)
        reason = strip_tags(note or "").strip() or "Assigned by staff"
        evaluation.assigned_physician = physician
        evaluation.save(update_fields=["assigned_physician", "updated_at"])
        if evaluation.status == EvaluationStatus.IN_REVIEW and evaluation.physician_id not in (
            None,
            physician.pk,
        ):
            evaluation.physician = None
            evaluation.claimed_at = None
            evaluation.save(update_fields=["physician", "claimed_at", "updated_at"])
            EvaluationService.transition(
                evaluation, EvaluationStatus.SUBMITTED, actor=user, reason=reason
            )
        else:
            evaluation.version += 1
            evaluation.save(update_fields=["version", "updated_at"])
        notify(
            physician,
            NotificationType.NEW_EVALUATION_IN_QUEUE,
            target_kind="clinical_evaluation",
            target_id=evaluation.pk,
            email=False,
        )
        record_audit(
            actor=user,
            action="evaluation.assigned",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            patient_id=evaluation.assessment.user_id,
            metadata={"physician_id": str(physician.pk)},
        )
    return evaluation


def unassign(
    *, evaluation_id: Any, reason: str, version: int, user: User, request: Any
) -> Evaluation:
    with transaction.atomic():
        evaluation = _locked(evaluation_id, version)
        if (
            evaluation.status != EvaluationStatus.SUBMITTED
            or evaluation.assigned_physician_id is None
        ):
            raise InvalidStateTransition()
        evaluation.assigned_physician = None
        evaluation.version += 1
        evaluation.save(update_fields=["assigned_physician", "version", "updated_at"])
        clinical.notify_queue(evaluation)
        record_audit(
            actor=user,
            action="evaluation.unassigned",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            patient_id=evaluation.assessment.user_id,
            metadata={"reason": strip_tags(reason or "").strip()},
        )
    return evaluation


def set_priority(*, evaluation_id: Any, priority: str, user: User, request: Any) -> Evaluation:
    with transaction.atomic():
        evaluation = _locked(evaluation_id, None)
        evaluation.priority = priority
        evaluation.priority_set_by_staff = True
        evaluation.save(update_fields=["priority", "priority_set_by_staff", "updated_at"])
        record_audit(
            actor=user,
            action="evaluation.priority_changed",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            changed_fields=["priority"],
        )
    return evaluation


def request_labs(
    *, evaluation_id: Any, version: int, data: dict[str, Any], user: User, request: Any
) -> Evaluation:
    catalog = {panel["code"]: panel for panel in clinical.lab_panel_catalog()}
    codes = list(dict.fromkeys(data["panels"]))
    unknown = [code for code in codes if code not in catalog]
    if unknown:
        raise ValidationError({"panels": [f"Unknown panel codes: {', '.join(unknown)}."]})
    with transaction.atomic():
        evaluation = _locked(evaluation_id, version)
        if evaluation.status != EvaluationStatus.SUBMITTED:
            raise InvalidStateTransition()
        evaluation.lab_recommendations = [catalog[code] for code in codes]
        evaluation.lab_recommendations_source = "PHYSICIAN"
        fields = ["lab_recommendations", "lab_recommendations_source", "updated_at"]
        message = strip_tags(data.get("message") or "").strip()
        if message:
            evaluation.patient_note = message
            fields.append("patient_note")
        evaluation.save(update_fields=fields)
        assessment = evaluation.assessment
        assessment.labs_status = LabsStatus.NOT_STARTED
        assessment.save(update_fields=["labs_status", "updated_at"])
        EvaluationService.transition(
            evaluation, EvaluationStatus.PENDING_LABS, actor=user, reason="Labs requested by staff"
        )
        record_audit(
            actor=user,
            action="evaluation.labs_requested",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            patient_id=assessment.user_id,
            metadata={"panels": codes},
        )
    return evaluation


def reopen(*, evaluation_id: Any, version: int, note: str, user: User, request: Any) -> Evaluation:
    with transaction.atomic():
        evaluation = _locked(evaluation_id, version)
        if evaluation.status != EvaluationStatus.SCREENED_OUT:
            raise InvalidStateTransition()
        program = evaluation.assessment.program
        fee = EvaluationFee.objects.select_for_update().filter(evaluation=evaluation).first()
        waived = program.fee_is_waived or program.fee_amount == 0
        if fee is not None:
            if waived:
                fee.status = FeeStatus.WAIVED if program.fee_is_waived else FeeStatus.NOT_REQUIRED
            elif fee.status not in (FeeStatus.PAID, FeeStatus.WAIVED):
                fee.status = FeeStatus.PENDING
                fee.amount = program.fee_amount
                fee.currency = program.fee_currency
            fee.save()
        paid = fee is not None and fee.status in (
            FeeStatus.PAID,
            FeeStatus.WAIVED,
            FeeStatus.NOT_REQUIRED,
        )
        target = EvaluationStatus.SUBMITTED if paid else EvaluationStatus.AWAITING_PAYMENT
        EvaluationService.transition(
            evaluation, target, actor=user, reason=strip_tags(note).strip()
        )
        EvaluationReviewRequest.objects.filter(
            evaluation=evaluation, resolved_at__isnull=True
        ).update(resolved_at=timezone.now())
        record_audit(
            actor=user,
            action="evaluation.reopened",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            patient_id=evaluation.assessment.user_id,
        )
    return evaluation
