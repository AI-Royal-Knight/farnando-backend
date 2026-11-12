"""Evaluation queue, claims, review packet and decisions (contract Part 4, section 4)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.assessments.enums import (
    EvaluationStatus,
    FeeStatus,
    LabsStatus,
    LabUploadStatus,
    QuestionType,
    ReviewGroup,
    ScreeningEffect,
    YesNo,
)
from apps.assessments.models import (
    AssessmentAnswer,
    AssessmentMeasurement,
    Evaluation,
    LabUpload,
    Prescription,
    Program,
)
from apps.assessments.services import (
    EvaluationService,
    age_on,
    display_height,
    display_weight,
    lab_recommendations,
)
from apps.catalog import runtime
from apps.clinical.models import PrescriptionDocument, UploadStatus
from apps.consultations.clinicians import physician_summary
from apps.consultations.models import ClinicianProfile, PhysicianVerificationStatus
from apps.core.audit import record_audit
from apps.core.exceptions import (
    AlreadyClaimed,
    AssignedToOther,
    ClaimRequired,
    DoseOutOfRange,
    FlagsNotAcknowledged,
    InvalidStateTransition,
    PreconditionFailed,
    PreconditionRequired,
)
from apps.treatments.models import (
    DoseFrequency,
    Product,
    Treatment,
    TreatmentStatus,
    TreatmentVersion,
)

OPEN_MINE_STATUSES = (
    EvaluationStatus.SUBMITTED,
    EvaluationStatus.IN_REVIEW,
    EvaluationStatus.PENDING_LABS,
)
GROUP_ORDER = [
    ReviewGroup.PRIMARY_CONDITIONS,
    ReviewGroup.CONTRAINDICATION_SCREENING,
    ReviewGroup.CURRENT_MEDICATIONS,
    ReviewGroup.WEIGHT_LOSS_HISTORY,
]
ROUTE_SHORT = {
    "SUBCUTANEOUS": "SubQ",
    "ORAL": "Oral",
    "SUBLINGUAL": "Sublingual",
    "TOPICAL": "Topical",
    "INTRAMUSCULAR": "IM",
}
FREQUENCY_LABEL = {
    DoseFrequency.DAILY: "Daily",
    DoseFrequency.WEEKLY: "Once weekly",
    DoseFrequency.EVERY_TWO_WEEKS: "Every two weeks",
    DoseFrequency.MONTHLY: "Monthly",
    DoseFrequency.AS_NEEDED: "As needed",
}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def parse_if_match(header: str | None) -> int:
    if not header:
        raise PreconditionRequired()
    match = re.fullmatch(r'\s*(?:W/)?"?(\d+)"?\s*', header)
    if match is None:
        raise PreconditionFailed()
    return int(match.group(1))


def check_version(evaluation: Evaluation, expected: int) -> None:
    if evaluation.version != expected:
        raise PreconditionFailed(details={"current_version": evaluation.version})


def touch(evaluation: Evaluation) -> None:
    evaluation.last_activity_at = timezone.now()
    evaluation.save(update_fields=["last_activity_at", "updated_at"])


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


def program_filter(profile: ClinicianProfile, prefix: str = "assessment__program") -> Q:
    open_programs = Program.objects.filter(allowed_specialties__isnull=True).values("pk")
    condition = Q(**{f"{prefix}__in": open_programs})
    if profile.specialty_ref_id is not None:
        condition |= Q(**{f"{prefix}__allowed_specialties": profile.specialty_ref_id})
    return condition


def _base() -> QuerySet[Evaluation]:
    clean = LabUpload.objects.filter(
        assessment=OuterRef("assessment"), is_deleted=False, status=LabUploadStatus.CLEAN
    )
    scanning = LabUpload.objects.filter(
        assessment=OuterRef("assessment"), is_deleted=False, status=LabUploadStatus.SCANNING
    )
    return (
        Evaluation.objects.select_related(
            "assessment__user",
            "assessment__program",
            "assessment__measurement",
            "physician__clinician_profile",
        )
        .annotate(has_clean_labs=Exists(clean), has_scanning_labs=Exists(scanning))
        .distinct()
    )


def visible_to(user: User, profile: ClinicianProfile) -> Q:
    """Amendment B5: reserved for the caller, or unreserved in an allowed program."""

    return Q(assigned_physician=user) | (
        Q(assigned_physician__isnull=True) & program_filter(profile)
    )


def queue_for(user: User, profile: ClinicianProfile) -> QuerySet[Evaluation]:
    return _base().filter(
        visible_to(user, profile), status=EvaluationStatus.SUBMITTED, physician__isnull=True
    )


def mine_for(user: User) -> QuerySet[Evaluation]:
    return _base().filter(physician=user, status__in=OPEN_MINE_STATUSES)


def filter_labs(queryset: QuerySet[Evaluation], state: str) -> QuerySet[Evaluation]:
    # Annotated fields; dict kwargs keep the type checker from resolving model fields.
    if state == "ATTACHED":
        return queryset.filter(**{"has_clean_labs": True})
    if state == "SCANNING":
        return queryset.filter(**{"has_clean_labs": False, "has_scanning_labs": True})
    return queryset.filter(**{"has_clean_labs": False, "has_scanning_labs": False})


def labs_state(evaluation: Evaluation) -> str:
    clean = getattr(evaluation, "has_clean_labs", None)
    scanning = getattr(evaluation, "has_scanning_labs", None)
    if clean is None:
        uploads = LabUpload.objects.filter(assessment_id=evaluation.assessment_id, is_deleted=False)
        clean = uploads.filter(status=LabUploadStatus.CLEAN).exists()
        scanning = uploads.filter(status=LabUploadStatus.SCANNING).exists()
    if clean:
        return "ATTACHED"
    return "SCANNING" if scanning else "MISSING"


def measurement_of(evaluation: Evaluation) -> AssessmentMeasurement | None:
    try:
        return evaluation.assessment.measurement
    except AssessmentMeasurement.DoesNotExist:
        return None


def patient_brief(user: User, measurement: AssessmentMeasurement | None = None) -> dict[str, Any]:
    if measurement is None:
        from apps.patients.services import latest_measurement

        measurement = latest_measurement(user)
    return {
        "id": user.pk,
        "name": user.get_full_name(),
        "age_years": age_on(measurement.date_of_birth) if measurement else None,
        "sex": measurement.sex if measurement else None,
    }


def item_payload(evaluation: Evaluation) -> dict[str, Any]:
    assessment = evaluation.assessment
    measurement = measurement_of(evaluation)
    return {
        "id": evaluation.pk,
        "version": evaluation.version,
        "status": evaluation.status,
        "patient": patient_brief(assessment.user, measurement),
        "program": {"id": assessment.program_id, "name": assessment.program.name},
        "submitted_at": evaluation.submitted_at,
        "questionnaire_state": "COMPLETE",
        "labs_state": labs_state(evaluation),
        "bmi": float(measurement.bmi) if measurement else None,
        "bmi_category": measurement.bmi_category if measurement else None,
        "screening_result": assessment.screening_result,
        "assigned_physician": (
            physician_summary(evaluation.physician) if evaluation.physician else None
        ),
    }


def eligible_physicians(evaluation: Evaluation) -> QuerySet[User]:
    if evaluation.assigned_physician_id is not None:
        return User.objects.filter(pk=evaluation.assigned_physician_id, is_active=True)
    program = evaluation.assessment.program
    profiles = ClinicianProfile.objects.filter(
        verification_status=PhysicianVerificationStatus.VERIFIED,
        user__role="PHYSICIAN",
        user__is_active=True,
    )
    specialties = list(program.allowed_specialties.values_list("pk", flat=True))
    if specialties:
        profiles = profiles.filter(specialty_ref_id__in=specialties)
    return User.objects.filter(pk__in=profiles.values("user_id"))


def notify_queue(evaluation: Evaluation) -> None:
    """``NEW_EVALUATION_IN_QUEUE`` to every physician who can claim it."""

    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    if evaluation.physician_id is not None:
        return
    for physician in eligible_physicians(evaluation):
        notify(
            physician,
            NotificationType.NEW_EVALUATION_IN_QUEUE,
            target_kind="clinical_evaluation",
            target_id=evaluation.pk,
            email=False,
        )


# ---------------------------------------------------------------------------
# Claim / release
# ---------------------------------------------------------------------------


def _locked(evaluation_id: Any) -> Evaluation:
    evaluation = (
        Evaluation.objects.select_for_update(of=("self",))
        .select_related("assessment__program", "assessment__user")
        .filter(pk=evaluation_id)
        .first()
    )
    if evaluation is None:
        raise NotFound()
    return evaluation


def claim(*, evaluation_id: Any, user: User, profile: ClinicianProfile, request: Any) -> Evaluation:
    with transaction.atomic():
        evaluation = _locked(evaluation_id)
        visible = Evaluation.objects.filter(pk=evaluation.pk).filter(
            Q(physician=user) | visible_to(user, profile) | ~Q(assigned_physician=None)
        )
        if not visible.exists():
            raise NotFound()
        if evaluation.physician_id not in (None, user.pk):
            raise AlreadyClaimed()
        if evaluation.assigned_physician_id not in (None, user.pk):
            raise AssignedToOther()
        if evaluation.physician_id == user.pk and evaluation.status in (
            EvaluationStatus.IN_REVIEW,
            EvaluationStatus.PENDING_LABS,
        ):
            touch(evaluation)
            return evaluation
        if evaluation.status != EvaluationStatus.SUBMITTED:
            raise InvalidStateTransition()
        now = timezone.now()
        evaluation.physician = user
        evaluation.claimed_at = now
        evaluation.last_activity_at = now
        evaluation.save(update_fields=["physician", "claimed_at", "last_activity_at", "updated_at"])
        EvaluationService.transition(
            evaluation, EvaluationStatus.IN_REVIEW, actor=user, reason="Claimed"
        )
        record_audit(
            actor=user,
            action="evaluation.claimed",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
        )
    return evaluation


def _assignee(evaluation: Evaluation, user: User) -> None:
    if evaluation.physician_id != user.pk:
        if evaluation.physician_id is None:
            raise ClaimRequired()
        raise NotFound()


def release(
    *, evaluation_id: Any, user: User, version: int, reason: str, request: Any
) -> Evaluation:
    with transaction.atomic():
        evaluation = _locked(evaluation_id)
        _assignee(evaluation, user)
        check_version(evaluation, version)
        if evaluation.status != EvaluationStatus.IN_REVIEW:
            raise InvalidStateTransition()
        _release(evaluation, actor=user, reason=strip_tags(reason or "").strip() or "Released")
        record_audit(
            actor=user,
            action="evaluation.released",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
        )
    return evaluation


def _release(evaluation: Evaluation, *, actor: User | None, reason: str) -> None:
    if evaluation.assigned_physician_id == evaluation.physician_id:
        evaluation.assigned_physician = None
    evaluation.physician = None
    evaluation.claimed_at = None
    evaluation.save(update_fields=["physician", "assigned_physician", "claimed_at", "updated_at"])
    EvaluationService.transition(evaluation, EvaluationStatus.SUBMITTED, actor=actor, reason=reason)


def auto_release_stale_claims(now: Any = None) -> int:
    now = now or timezone.now()
    cutoff = now - timedelta(hours=runtime.hours("evaluation", "claim_timeout_hours"))
    count = 0
    stale = Evaluation.objects.filter(status=EvaluationStatus.IN_REVIEW).filter(
        Q(last_activity_at__lt=cutoff) | Q(last_activity_at__isnull=True, claimed_at__lt=cutoff)
    )
    for evaluation in stale:
        with transaction.atomic():
            locked = _locked(evaluation.pk)
            if locked.status == EvaluationStatus.IN_REVIEW:
                _release(locked, actor=None, reason="Claim expired")
                count += 1
    return count


# ---------------------------------------------------------------------------
# Review packet
# ---------------------------------------------------------------------------


def _render_answer(answer: AssessmentAnswer) -> str:
    question = answer.question
    if question.type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        labels = {str(option.id): option.label for option in question.options.all()}
        values = answer.option_ids if isinstance(answer.option_ids, list) else []
        return ", ".join(labels.get(str(value), "") for value in values if str(value) in labels)
    if question.type == QuestionType.YES_NO:
        return "Yes" if answer.answer == YesNo.YES else "No" if answer.answer else ""
    return str(answer.text)


def _flags(answer: AssessmentAnswer) -> list[dict[str, Any]]:
    question = answer.question
    flags: list[dict[str, Any]] = []
    if question.type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        options = {str(option.id): option for option in question.options.all()}
        values = answer.option_ids if isinstance(answer.option_ids, list) else []
        for value in values:
            option = options.get(str(value))
            if option is not None and option.screening_effect != ScreeningEffect.NONE:
                flags.append(
                    {
                        "question_id": question.pk,
                        "question_text": question.text,
                        "option_id": option.pk,
                        "option_label": option.label,
                        "effect": option.screening_effect,
                    }
                )
    elif question.type == QuestionType.YES_NO:
        effect = question.yes_effect if answer.answer == YesNo.YES else question.no_effect
        if answer.answer and effect != ScreeningEffect.NONE:
            flags.append(
                {
                    "question_id": question.pk,
                    "question_text": question.text,
                    "option_id": None,
                    "option_label": "Yes" if answer.answer == YesNo.YES else "No",
                    "effect": effect,
                }
            )
    elif answer.text and question.screening_effect != ScreeningEffect.NONE:
        flags.append(
            {
                "question_id": question.pk,
                "question_text": question.text,
                "option_id": None,
                "option_label": answer.text[:200],
                "effect": question.screening_effect,
            }
        )
    return flags


def _answers(evaluation: Evaluation) -> list[AssessmentAnswer]:
    return list(
        AssessmentAnswer.objects.filter(assessment=evaluation.assessment)
        .select_related("question__step")
        .prefetch_related("question__options")
        .order_by("question__step__position", "question__position")
    )


def screening_flags(evaluation: Evaluation) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for answer in _answers(evaluation):
        flags.extend(_flags(answer))
    return flags


def biometrics(measurement: AssessmentMeasurement | None, unit: str) -> dict[str, Any] | None:
    if measurement is None:
        return None
    height_unit = "IN" if unit == "LB" else "CM"
    return {
        "current_weight": display_weight(measurement.current_weight_kg, unit),
        "target_weight": display_weight(measurement.target_weight_kg, unit),
        "weight_goal_kg": float(measurement.weight_goal_kg),
        "height": display_height(measurement.height_cm, height_unit),
        "bmi": float(measurement.bmi),
        "bmi_category": measurement.bmi_category,
    }


def lab_attachments(assessment_ids: list[Any]) -> list[dict[str, Any]]:
    uploads = LabUpload.objects.filter(
        assessment_id__in=assessment_ids, is_deleted=False, status=LabUploadStatus.CLEAN
    ).order_by("-created_at")
    return [
        {
            "id": upload.pk,
            "file_name": upload.file_name,
            "size_bytes": upload.size_bytes,
            "content_type": upload.content_type,
            "uploaded_by": "PATIENT",
            "uploaded_at": upload.created_at,
            "status": "CLEAN",
        }
        for upload in uploads
    ]


def questionnaire_blocks(evaluation: Evaluation) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = {group: [] for group in [*GROUP_ORDER, "OTHER"]}
    for answer in _answers(evaluation):
        rendered = _render_answer(answer)
        if not rendered:
            continue
        group = answer.question.review_group or ReviewGroup.OTHER
        groups.setdefault(group, []).append(
            {"question_text": answer.question.text, "answer": rendered}
        )
    blocks: list[dict[str, Any]] = [
        {"group": group, "title": ReviewGroup(group).label, "items": groups[group]}
        for group in GROUP_ORDER
    ]
    if groups["OTHER"]:
        blocks.append({"group": "OTHER", "title": "Other", "items": groups["OTHER"]})
    return blocks


def packet(*, evaluation_id: Any, user: User, request: Any) -> dict[str, Any]:
    evaluation = _base().filter(pk=evaluation_id).first()
    if evaluation is None:
        raise NotFound()
    if user.role != "ADMIN":
        _assignee(evaluation, user)
        touch(evaluation)
    record_audit(
        actor=user,
        action="evaluation.packet_read",
        object_type="Evaluation",
        object_id=evaluation.pk,
        request=request,
    )
    assessment = evaluation.assessment
    patient = assessment.user
    measurement = measurement_of(evaluation)
    profile = ClinicianProfile.objects.filter(user=user).first()
    unit = profile.preferred_weight_unit if profile else "KG"

    blocks = questionnaire_blocks(evaluation)

    panels = lab_recommendations(evaluation)["panels"]
    previous = (
        Evaluation.objects.filter(assessment__user=patient)
        .exclude(pk=evaluation.pk)
        .select_related("assessment__program")
        .order_by("-submitted_at")
    )
    treatments = Treatment.objects.filter(patient=patient).order_by("-started_at")
    body = item_payload(evaluation)
    body.update(
        {
            "patient": {
                **patient_brief(patient, measurement),
                "date_of_birth": measurement.date_of_birth if measurement else None,
            },
            "biometrics": biometrics(measurement, unit),
            "questionnaire": blocks,
            "screening_flags": screening_flags(evaluation),
            "attachments": lab_attachments([assessment.pk]),
            "lab_recommendations": [
                {"code": str(panel.get("code", "")), "name": str(panel.get("name", ""))}
                for panel in panels
                if isinstance(panel, dict)
            ],
            "previous_evaluations": [
                {
                    "id": item.pk,
                    "program_name": item.assessment.program.name,
                    "status": item.status,
                    "decided_at": item.decided_at,
                }
                for item in previous
            ],
            "existing_treatments": [
                {
                    "treatment_id": item.pk,
                    "medication_name": item.medication_name,
                    "status": item.status,
                }
                for item in treatments
            ],
            "patient_note": evaluation.patient_note or None,
            "internal_note": evaluation.internal_note or None,
        }
    )
    return body


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def lab_panel_catalog() -> list[dict[str, str]]:
    from apps.catalog.models import LabPanel

    panels = list(LabPanel.objects.filter(is_active=True).order_by("position", "code"))
    if panels:
        return [
            {"code": panel.code, "name": panel.name, "description": panel.description}
            for panel in panels
        ]
    seen: dict[str, dict[str, str]] = {}
    for program in Program.objects.filter(is_active=True).order_by("position", "name"):
        panels = program.lab_panels if isinstance(program.lab_panels, list) else []
        for panel in panels:
            if isinstance(panel, dict) and panel.get("code") and panel["code"] not in seen:
                seen[str(panel["code"])] = {
                    "code": str(panel["code"]),
                    "name": str(panel.get("name", "")),
                    "description": str(panel.get("description", "")),
                }
    return list(seen.values())


def _decision_target(evaluation_id: Any, user: User, version: int) -> Evaluation:
    evaluation = _locked(evaluation_id)
    _assignee(evaluation, user)
    check_version(evaluation, version)
    if evaluation.status != EvaluationStatus.IN_REVIEW:
        raise InvalidStateTransition()
    return evaluation


def request_labs(
    *, evaluation_id: Any, user: User, version: int, data: dict[str, Any], request: Any
) -> Evaluation:
    catalog = {panel["code"]: panel for panel in lab_panel_catalog()}
    codes = list(dict.fromkeys(data["panels"]))
    unknown = [code for code in codes if code not in catalog]
    if unknown:
        raise ValidationError({"panels": [f"Unknown panel codes: {', '.join(unknown)}."]})
    with transaction.atomic():
        evaluation = _decision_target(evaluation_id, user, version)
        evaluation.lab_recommendations = [catalog[code] for code in codes]
        evaluation.lab_recommendations_source = "PHYSICIAN"
        message = strip_tags(data.get("message") or "").strip()
        fields = ["lab_recommendations", "lab_recommendations_source", "updated_at"]
        if message:
            evaluation.patient_note = message
            fields.append("patient_note")
        evaluation.save(update_fields=fields)
        assessment = evaluation.assessment
        assessment.labs_status = LabsStatus.NOT_STARTED
        assessment.save(update_fields=["labs_status", "updated_at"])
        EvaluationService.transition(
            evaluation, EvaluationStatus.PENDING_LABS, actor=user, reason="Labs requested"
        )
        touch(evaluation)
        record_audit(
            actor=user,
            action="evaluation.labs_requested",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            metadata={"panels": codes},
        )
    return evaluation


def reject(
    *, evaluation_id: Any, user: User, version: int, data: dict[str, Any], request: Any
) -> Evaluation:
    with transaction.atomic():
        evaluation = _decision_target(evaluation_id, user, version)
        evaluation.rejection_reason_code = data["reason_code"]
        evaluation.internal_note = strip_tags(data["internal_note"]).strip()
        evaluation.patient_note = strip_tags(data.get("patient_note") or "").strip()
        evaluation.save(
            update_fields=["rejection_reason_code", "internal_note", "patient_note", "updated_at"]
        )
        EvaluationService.transition(
            evaluation, EvaluationStatus.NOT_SUITABLE, actor=user, reason=data["reason_code"]
        )
        touch(evaluation)
        record_audit(
            actor=user,
            action="evaluation.rejected",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            metadata={"reason_code": data["reason_code"]},
        )
        from apps.clinical.tasks import send_staff_alert

        transaction.on_commit(lambda: send_staff_alert.delay("evaluation_rejected"))
        _refund_task_if_configured(evaluation)
    return evaluation


def _refund_task_if_configured(evaluation: Evaluation) -> None:
    """Part 5, 7.3: optional refund task for a paid fee on NOT_SUITABLE."""

    from apps.catalog import runtime

    if not runtime.value("payments", "refund_fee_on_not_suitable"):
        return
    fee = getattr(evaluation, "fee", None)
    if fee is None or fee.status != FeeStatus.PAID:
        return
    from apps.support.tickets import create_refund_task

    create_refund_task(
        title="Evaluation fee refund (not suitable)", patient=evaluation.assessment.user
    )


def _dose_key(value: Any, unit: Any) -> tuple[Decimal, str]:
    try:
        return Decimal(str(value)).normalize(), str(unit).strip().lower()
    except (InvalidOperation, ValueError) as exc:
        raise DoseOutOfRange() from exc


def dose_in(dose: dict[str, Any], options: Any) -> dict[str, Any] | None:
    target = _dose_key(dose.get("value"), dose.get("unit"))
    for option in options if isinstance(options, list) else []:
        if (
            isinstance(option, dict)
            and _dose_key(option.get("value"), option.get("unit")) == target
        ):
            return option
    return None


def dose_label(dose: dict[str, Any], option: dict[str, Any] | None) -> str:
    if option and option.get("label"):
        return str(option["label"])
    value = Decimal(str(dose["value"])).normalize()
    return f"{value:f} {dose['unit']}"


def _months_ahead(start: date, months: int) -> date:
    import calendar

    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def approve(
    *, evaluation_id: Any, user: User, version: int, data: dict[str, Any], request: Any
) -> tuple[Evaluation, Prescription, list[Treatment]]:
    prescription_data = data["prescription"]
    today = timezone.localdate()
    start_date: date = prescription_data["start_date"]
    valid_until: date = prescription_data["valid_until"]
    if not today <= start_date <= today + timedelta(days=settings.PRESCRIPTION_MAX_START_DAYS):
        raise ValidationError({"prescription.start_date": ["Must be today to 30 days ahead."]})
    if not (
        start_date
        < valid_until
        <= _months_ahead(start_date, settings.PRESCRIPTION_MAX_VALIDITY_MONTHS)
    ):
        raise ValidationError(
            {"prescription.valid_until": ["Must be after start_date and at most 12 months."]}
        )

    with transaction.atomic():
        evaluation = _decision_target(evaluation_id, user, version)
        flags = screening_flags(evaluation)
        acknowledged = {str(value) for value in data.get("acknowledged_flag_option_ids") or []}
        required = {str(flag["option_id"] or flag["question_id"]) for flag in flags}
        missing = sorted(required - acknowledged)
        if missing:
            raise FlagsNotAcknowledged(details={"missing": missing})

        program_id = evaluation.assessment.program_id
        items: list[tuple[dict[str, Any], Product, dict[str, Any] | None]] = []
        for index, item in enumerate(prescription_data["items"]):
            product = Product.objects.filter(
                pk=item["product_id"], is_active=True, programs__pk=program_id
            ).first()
            if product is None:
                raise ValidationError(
                    {f"prescription.items[{index}].product_id": ["Unknown product."]}
                )
            option = dose_in(item["dose"], product.starting_doses)
            if option is None:
                raise DoseOutOfRange(details={"item": index})
            if product.route and item["route"] != product.route:
                raise ValidationError(
                    {f"prescription.items[{index}].route": ["Does not match the product."]}
                )
            if item["refills_total"] > product.max_refills:
                raise ValidationError(
                    {
                        f"prescription.items[{index}].refills_total": [
                            f"At most {product.max_refills}."
                        ]
                    }
                )
            needs_day = item["frequency"] in (DoseFrequency.WEEKLY, DoseFrequency.EVERY_TWO_WEEKS)
            if needs_day and not item.get("day_of_week"):
                raise ValidationError(
                    {f"prescription.items[{index}].day_of_week": ["Required for this frequency."]}
                )
            items.append((item, product, option))

        document = None
        if data.get("signed_document_id"):
            document = PrescriptionDocument.objects.filter(
                pk=data["signed_document_id"],
                evaluation=evaluation,
                uploaded_by=user,
                status=UploadStatus.CLEAN,
            ).first()
            if document is None:
                raise ValidationError({"signed_document_id": ["Unknown or unscanned document."]})

        first_item, first_product, first_option = items[0]
        first_label = dose_label(first_item["dose"], first_option)
        prescription, _created = Prescription.objects.update_or_create(
            evaluation=evaluation,
            defaults={
                "medication_name": first_product.name,
                "dosage_label": first_label,
                "issued_by": user,
                "start_date": start_date,
                "valid_until": valid_until,
                "signed_document": document,
                "pdf_key": document.storage_key if document else "",
                "can_download": True,
                "can_order": True,
            },
        )
        treatments: list[Treatment] = []
        for item, product, option in items:
            label = dose_label(item["dose"], option)
            route_short = ROUTE_SHORT.get(item["route"], item["route"])
            instructions = strip_tags(item["instructions"]).strip()
            treatment = Treatment.objects.create(
                patient=evaluation.assessment.user,
                program_id=program_id,
                evaluation=evaluation,
                product=product,
                prescription=prescription,
                prescribing_physician=user,
                status=TreatmentStatus.ACTIVE,
                title=f"{product.name.split(' (')[0]} {label}",
                medication_name=product.name,
                strength_label=f"{label} {route_short}",
                route=item["route"],
                frequency=item["frequency"],
                day_of_week=item.get("day_of_week"),
                time_of_day=item["time_of_day"],
                schedule_label=f"{FREQUENCY_LABEL[item['frequency']]} ({route_short})",
                dose_value=Decimal(str(item["dose"]["value"])),
                dose_unit=item["dose"]["unit"],
                dose_label=label,
                instructions=instructions,
                duration_weeks=item["duration_weeks"],
                started_at=start_date,
                refills_total=item["refills_total"],
                refills_remaining=item["refills_total"],
                prescription_expires_at=valid_until,
            )
            TreatmentVersion.objects.create(
                treatment=treatment,
                version=1,
                dose_value=treatment.dose_value,
                dose_unit=treatment.dose_unit,
                dose_label=label,
                frequency=treatment.frequency,
                day_of_week=treatment.day_of_week,
                time_of_day=treatment.time_of_day,
                instructions=instructions,
                effective_from=start_date,
                applied_at=timezone.now(),
                reason="Initial prescription",
                changed_by=user,
            )
            treatments.append(treatment)

        evaluation.patient_note = strip_tags(data.get("patient_note") or "").strip()
        evaluation.internal_note = strip_tags(data.get("internal_note") or "").strip()
        evaluation.save(update_fields=["patient_note", "internal_note", "updated_at"])
        EvaluationService.transition(
            evaluation, EvaluationStatus.APPROVED, actor=user, reason="Approved"
        )
        touch(evaluation)
        record_audit(
            actor=user,
            action="evaluation.approved",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            metadata={
                "prescription_id": str(prescription.pk),
                "treatment_ids": [str(t.pk) for t in treatments],
                "acknowledged_flags": sorted(acknowledged & required),
            },
        )
        if document is None:
            from apps.clinical.tasks import generate_prescription_pdf

            prescription_id = str(prescription.pk)
            transaction.on_commit(lambda: generate_prescription_pdf.delay(prescription_id))
    return evaluation, prescription, treatments


__all__ = [
    "approve",
    "auto_release_stale_claims",
    "biometrics",
    "claim",
    "dose_in",
    "item_payload",
    "lab_attachments",
    "lab_panel_catalog",
    "labs_state",
    "mine_for",
    "notify_queue",
    "packet",
    "parse_if_match",
    "patient_brief",
    "queue_for",
    "reject",
    "release",
    "request_labs",
    "screening_flags",
]
