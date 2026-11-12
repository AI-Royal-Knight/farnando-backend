"""Assessment, screening, evaluation and fee business rules."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.accounts.services import ConsentService, LegalDocumentService
from apps.assessments.enums import (
    AssessmentStatus,
    BmiCategory,
    ChecklistState,
    EvaluationStatus,
    FeeStatus,
    HeightUnit,
    JourneyStep,
    LabsStatus,
    QuestionType,
    ScreeningEffect,
    ScreeningResult,
    StepType,
    WeightUnit,
    YesNo,
)
from apps.assessments.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentMeasurement,
    Evaluation,
    EvaluationFee,
    EvaluationReviewRequest,
    EvaluationStatusHistory,
    Program,
    QuestionnaireQuestion,
    QuestionnaireStep,
)
from apps.core.audit import record_audit
from apps.core.exceptions import (
    AssessmentIncomplete,
    AssessmentLocked,
    ConsentRequired,
    EmailNotVerified,
    InvalidStateTransition,
    TargetBelowHealthyRange,
    TargetWeightInvalid,
    UnderAge,
)

logger = structlog.get_logger(__name__)

LB_TO_KG = Decimal("0.45359237")
IN_TO_CM = Decimal("2.54")
MIN_WEIGHT_KG = Decimal("30")
MAX_WEIGHT_KG = Decimal("350")
MIN_HEIGHT_CM = Decimal("120")
MAX_HEIGHT_CM = Decimal("230")
MIN_TARGET_BMI = Decimal("18.5")

ACTIVE_EVALUATION_STATUSES = {
    EvaluationStatus.SCREENED_OUT,
    EvaluationStatus.AWAITING_PAYMENT,
    EvaluationStatus.SUBMITTED,
    EvaluationStatus.IN_REVIEW,
    EvaluationStatus.PENDING_LABS,
}
CANCELLABLE_EVALUATION_STATUSES = {
    EvaluationStatus.AWAITING_PAYMENT,
    EvaluationStatus.SUBMITTED,
    EvaluationStatus.PENDING_LABS,
}
LAB_UPLOAD_EVALUATION_STATUSES = {
    EvaluationStatus.SUBMITTED,
    EvaluationStatus.AWAITING_PAYMENT,
    EvaluationStatus.IN_REVIEW,
    EvaluationStatus.PENDING_LABS,
}

BMI_SCALE = [
    {"category": BmiCategory.UNDERWEIGHT, "label": "Underweight", "min": None, "max": 18.5},
    {"category": BmiCategory.HEALTHY, "label": "Healthy", "min": 18.5, "max": 24.9},
    {"category": BmiCategory.OVERWEIGHT, "label": "Overweight", "min": 25, "max": 29.9},
    {"category": BmiCategory.OBESE, "label": "Obese", "min": 30, "max": None},
]


def _q(value: Decimal, places: int) -> Decimal:
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def weight_to_kg(value: Decimal, unit: str) -> Decimal:
    if unit == WeightUnit.LB:
        return _q(value * LB_TO_KG, 2)
    return _q(value, 2)


def height_to_cm(value: Decimal, unit: str) -> Decimal:
    if unit == HeightUnit.IN:
        return _q(value * IN_TO_CM, 1)
    return _q(value, 1)


def display_weight(kg: Decimal, unit: str) -> dict[str, Any]:
    value = float(_q(kg / LB_TO_KG, 1)) if unit == WeightUnit.LB else float(_q(kg, 2))
    return {"kg": float(_q(kg, 2)), "display": {"value": value, "unit": unit}}


def display_height(cm: Decimal, unit: str) -> dict[str, Any]:
    value = float(_q(cm / IN_TO_CM, 1)) if unit == HeightUnit.IN else float(_q(cm, 1))
    return {"cm": float(_q(cm, 1)), "display": {"value": value, "unit": unit}}


def calculate_bmi(weight_kg: Decimal, height_cm: Decimal) -> Decimal:
    metres = height_cm / Decimal("100")
    return _q(weight_kg / (metres * metres), 1)


def bmi_category(bmi: Decimal) -> str:
    if bmi < Decimal("18.5"):
        return BmiCategory.UNDERWEIGHT
    if bmi < Decimal("25"):
        return BmiCategory.HEALTHY
    if bmi < Decimal("30"):
        return BmiCategory.OVERWEIGHT
    return BmiCategory.OBESE


def age_on(dob: date, on: date | None = None) -> int:
    today = on or timezone.localdate()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def money(amount: Decimal, currency: str) -> dict[str, str]:
    return {"amount": f"{_q(amount, 2):.2f}", "currency": currency}


@dataclass(frozen=True)
class AnswerSnapshot:
    option_ids: tuple[str, ...] = ()
    answer: str = ""
    text: str = ""


def _snapshot_from_model(answer: AssessmentAnswer) -> AnswerSnapshot:
    option_ids = answer.option_ids if isinstance(answer.option_ids, list) else []
    return AnswerSnapshot(
        option_ids=tuple(str(value) for value in option_ids),
        answer=answer.answer or "",
        text=answer.text or "",
    )


def _snapshot_from_payload(payload: dict[str, Any]) -> AnswerSnapshot:
    option_ids = payload.get("option_ids") or []
    return AnswerSnapshot(
        option_ids=tuple(str(value) for value in option_ids),
        answer=str(payload.get("answer") or ""),
        text=str(payload.get("text") or ""),
    )


def _answer_values(snapshot: AnswerSnapshot) -> set[str]:
    values = set(snapshot.option_ids)
    if snapshot.answer:
        values.add(snapshot.answer)
    if snapshot.text:
        values.add(snapshot.text)
    return values


def _has_answer(question: QuestionnaireQuestion, snapshot: AnswerSnapshot | None) -> bool:
    if snapshot is None:
        return False
    if question.type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        return bool(snapshot.option_ids)
    if question.type == QuestionType.YES_NO:
        return bool(snapshot.answer)
    return bool(snapshot.text.strip())


def _question_visible(
    question: QuestionnaireQuestion, snapshots: dict[str, AnswerSnapshot]
) -> bool:
    if question.show_if_id is None:
        return True
    parent = snapshots.get(str(question.show_if_id))
    if parent is None:
        return False
    equals = question.show_if_equals if isinstance(question.show_if_equals, list) else []
    return bool(_answer_values(parent) & {str(value) for value in equals})


def _snapshots_for(
    assessment: Assessment, extra: dict[str, AnswerSnapshot] | None = None
) -> dict[str, AnswerSnapshot]:
    snapshots = {
        str(answer.question_id): _snapshot_from_model(answer)
        for answer in AssessmentAnswer.objects.filter(assessment=assessment).select_related(
            "question"
        )
    }
    if extra:
        snapshots.update(extra)
    return snapshots


def visible_questions(
    step: QuestionnaireStep, snapshots: dict[str, AnswerSnapshot]
) -> list[QuestionnaireQuestion]:
    return [question for question in step.questions.all() if _question_visible(question, snapshots)]


def step_is_complete(
    assessment: Assessment,
    step: QuestionnaireStep,
    snapshots: dict[str, AnswerSnapshot] | None = None,
) -> bool:
    if step.type == StepType.MEASUREMENTS:
        return AssessmentMeasurement.objects.filter(assessment=assessment).exists()
    mapping = snapshots if snapshots is not None else _snapshots_for(assessment)
    for question in visible_questions(step, mapping):
        if question.required and not _has_answer(question, mapping.get(str(question.id))):
            return False
    return True


@dataclass(frozen=True)
class Progress:
    completed_steps: int
    total_steps: int
    missing_step_keys: list[str]
    current_step_key: str | None

    @property
    def is_complete(self) -> bool:
        return not self.missing_step_keys


def assessment_progress(assessment: Assessment) -> Progress:
    steps = list(assessment.questionnaire_version.steps.all())
    snapshots = _snapshots_for(assessment)
    missing: list[str] = []
    for step in steps:
        if not step_is_complete(assessment, step, snapshots):
            missing.append(step.key)
    current = missing[0] if missing else None
    return Progress(
        completed_steps=len(steps) - len(missing),
        total_steps=len(steps),
        missing_step_keys=missing,
        current_step_key=current,
    )


def current_step_key(assessment: Assessment) -> str | None:
    return assessment_progress(assessment).current_step_key


def _selected_option_effects(
    question: QuestionnaireQuestion, snapshot: AnswerSnapshot
) -> list[str]:
    effects: list[str] = []
    if question.type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        selected = {str(option.id): option for option in question.options.all()}
        for option_id in snapshot.option_ids:
            option = selected.get(option_id)
            if option is not None and option.screening_effect != ScreeningEffect.NONE:
                effects.append(option.screening_effect)
    elif question.type == QuestionType.YES_NO:
        if snapshot.answer == YesNo.YES and question.yes_effect != ScreeningEffect.NONE:
            effects.append(question.yes_effect)
        if snapshot.answer == YesNo.NO and question.no_effect != ScreeningEffect.NONE:
            effects.append(question.no_effect)
    elif question.screening_effect != ScreeningEffect.NONE:
        effects.append(question.screening_effect)
    return effects


def compute_screening(assessment: Assessment) -> ScreeningResult | None:
    progress = assessment_progress(assessment)
    if not progress.is_complete:
        return None
    snapshots = _snapshots_for(assessment)
    effects: list[str] = []
    for step in assessment.questionnaire_version.steps.all():
        if step.type != StepType.QUESTIONS:
            continue
        for question in visible_questions(step, snapshots):
            snapshot = snapshots.get(str(question.id))
            if snapshot is not None:
                effects.extend(_selected_option_effects(question, snapshot))
    measurement = AssessmentMeasurement.objects.filter(assessment=assessment).first()
    program = assessment.program
    if (
        measurement is not None
        and program.min_bmi is not None
        and measurement.bmi < program.min_bmi
    ):
        effects.append(program.min_bmi_effect)
    if (
        measurement is not None
        and program.max_bmi is not None
        and measurement.bmi > program.max_bmi
    ):
        effects.append(program.min_bmi_effect)
    if ScreeningEffect.NOT_ELIGIBLE in effects:
        return ScreeningResult.NOT_ELIGIBLE
    if ScreeningEffect.NEEDS_REVIEW in effects:
        return ScreeningResult.NEEDS_REVIEW
    return ScreeningResult.ELIGIBLE


def refresh_screening(assessment: Assessment) -> ScreeningResult | None:
    result = compute_screening(assessment)
    if assessment.screening_result != result:
        assessment.screening_result = result
        assessment.save(update_fields=["screening_result", "updated_at"])
    return result


def has_assessment_consents(user: User) -> bool:
    current = LegalDocumentService.current_versions()
    required = (
        "TERMS_OF_SERVICE",
        "PRIVACY_POLICY",
        "HEALTH_DATA_PROCESSING",
    )
    if not all(document_type in current for document_type in required):
        return False
    accepted = {*ConsentService.REQUIRED, "HEALTH_DATA_PROCESSING"}
    records = set(
        user.consents.filter(document_type__in=accepted).values_list("document_type", "version")
    )
    return all((document_type, current[document_type]) in records for document_type in required)


def create_or_resume_assessment(*, user: User, program_id: Any) -> tuple[Assessment, bool]:
    program = Program.objects.filter(pk=program_id, is_active=True).first()
    if program is None or not program.is_available:
        raise NotFound()
    if not has_assessment_consents(user):
        raise ConsentRequired()
    questionnaire = program.published_questionnaire
    if questionnaire is None:
        raise NotFound()

    with transaction.atomic():
        draft = (
            Assessment.objects.select_for_update()
            .filter(
                user=user,
                program=program,
                status=AssessmentStatus.DRAFT,
            )
            .first()
        )
        if draft is not None:
            return draft, False

        active = Evaluation.objects.filter(
            assessment__user=user,
            assessment__program=program,
            status__in=ACTIVE_EVALUATION_STATUSES,
        ).first()
        if active is not None:
            from apps.core.exceptions import Conflict

            raise Conflict("An active evaluation already exists for this program.")

        recent = Evaluation.objects.filter(
            assessment__user=user,
            assessment__program=program,
            status=EvaluationStatus.NOT_SUITABLE,
            decided_at__gte=timezone.now() - timedelta(days=program.not_suitable_cooldown_days),
        ).first()
        if recent is not None:
            from apps.core.exceptions import Conflict

            raise Conflict("The assessment cooldown has not elapsed.")

        try:
            with transaction.atomic():
                assessment = Assessment.objects.create(
                    user=user,
                    program=program,
                    questionnaire_version=questionnaire,
                    status=AssessmentStatus.DRAFT,
                )
        except IntegrityError:
            assessment = Assessment.objects.get(
                user=user, program=program, status=AssessmentStatus.DRAFT
            )
            return assessment, False
        return assessment, True


def save_measurements(assessment: Assessment, data: dict[str, Any]) -> AssessmentMeasurement:
    if assessment.status != AssessmentStatus.DRAFT:
        raise AssessmentLocked()

    program = assessment.program
    current_kg = weight_to_kg(data["current_weight"]["value"], data["current_weight"]["unit"])
    target_kg = weight_to_kg(data["target_weight"]["value"], data["target_weight"]["unit"])
    height_cm = height_to_cm(data["height"]["value"], data["height"]["unit"])
    dob: date = data["date_of_birth"]

    if not (MIN_WEIGHT_KG <= current_kg <= MAX_WEIGHT_KG):
        raise ValidationError({"current_weight": ["Current weight must be between 30 and 350 kg."]})
    if not (MIN_WEIGHT_KG <= target_kg <= MAX_WEIGHT_KG):
        raise ValidationError({"target_weight": ["Target weight must be between 30 and 350 kg."]})
    if not (MIN_HEIGHT_CM <= height_cm <= MAX_HEIGHT_CM):
        raise ValidationError({"height": ["Height must be between 120 and 230 cm."]})
    if dob > timezone.localdate():
        raise ValidationError({"date_of_birth": ["Date of birth cannot be in the future."]})
    age = age_on(dob)
    if age < program.age_min:
        raise UnderAge()
    if age > program.age_max:
        raise ValidationError(
            {"date_of_birth": [f"Patients older than {program.age_max} require in-person care."]}
        )

    if program.requires_target_lower and target_kg >= current_kg:
        raise TargetWeightInvalid()
    current_bmi = calculate_bmi(current_kg, height_cm)
    target_bmi = calculate_bmi(target_kg, height_cm)
    if target_bmi < MIN_TARGET_BMI:
        raise TargetBelowHealthyRange()

    with transaction.atomic():
        measurement, _created = AssessmentMeasurement.objects.update_or_create(
            assessment=assessment,
            defaults={
                "current_weight_kg": current_kg,
                "target_weight_kg": target_kg,
                "height_cm": height_cm,
                "sex": data["sex"],
                "date_of_birth": dob,
                "bmi": current_bmi,
                "bmi_category": bmi_category(current_bmi),
                "target_bmi": target_bmi,
                "weight_goal_kg": _q(current_kg - target_kg, 2),
            },
        )
        assessment.display_weight_unit = data["current_weight"]["unit"]
        assessment.display_height_unit = data["height"]["unit"]
        assessment.save(update_fields=["display_weight_unit", "display_height_unit", "updated_at"])
        refresh_screening(assessment)
    return measurement


def _validate_answer(question: QuestionnaireQuestion, body: dict[str, Any]) -> AnswerSnapshot:
    provided = [key for key in ("option_ids", "answer", "text") if key in body]
    if len(provided) != 1:
        raise ValidationError(
            {"answers": ["Each answer must use exactly one of option_ids, answer or text."]}
        )

    if question.type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
        if "option_ids" not in body:
            raise ValidationError({"answers": ["option_ids is required for this question."]})
        option_ids = [str(value) for value in body.get("option_ids") or []]
        if len(option_ids) != len(set(option_ids)):
            raise ValidationError({"answers": ["Duplicate option ids are not allowed."]})
        allowed = {str(option.id): option for option in question.options.all()}
        unknown = [value for value in option_ids if value not in allowed]
        if unknown:
            raise ValidationError({"answers": ["Unknown option for this question."]})
        if question.type == QuestionType.SINGLE_CHOICE and len(option_ids) > 1:
            raise ValidationError({"answers": ["SINGLE_CHOICE questions accept only one option."]})
        exclusive = [value for value in option_ids if allowed[value].is_exclusive]
        if exclusive and len(option_ids) > 1:
            raise ValidationError(
                {"answers": ["An exclusive option cannot be combined with other options."]}
            )
        return AnswerSnapshot(option_ids=tuple(option_ids))

    if question.type == QuestionType.YES_NO:
        if "answer" not in body:
            raise ValidationError({"answers": ["answer is required for this question."]})
        answer = str(body.get("answer") or "")
        if answer not in (YesNo.YES, YesNo.NO):
            raise ValidationError({"answers": ["answer must be YES or NO."]})
        return AnswerSnapshot(answer=answer)

    if "text" not in body:
        raise ValidationError({"answers": ["text is required for this question."]})
    cleaned = strip_tags(str(body.get("text") or "")).strip()
    if not cleaned:
        raise ValidationError({"answers": ["Text answers cannot be blank."]})
    if question.max_length is not None and len(cleaned) > question.max_length:
        raise ValidationError(
            {"answers": [f"Text answers must be at most {question.max_length} characters."]}
        )
    return AnswerSnapshot(text=cleaned)


def save_step(
    assessment: Assessment, step_key: str, answers_payload: list[dict[str, Any]]
) -> tuple[QuestionnaireStep, dict[str, AnswerSnapshot], bool]:
    if assessment.status != AssessmentStatus.DRAFT:
        raise AssessmentLocked()

    step = (
        QuestionnaireStep.objects.filter(
            questionnaire=assessment.questionnaire_version, key=step_key
        )
        .prefetch_related("questions__options")
        .first()
    )
    if step is None:
        raise NotFound()
    if step.type != StepType.QUESTIONS:
        raise ValidationError({"step_key": ["This step does not accept answers."]})

    questions = {str(question.id): question for question in step.questions.all()}
    candidates: dict[str, dict[str, Any]] = {}
    for payload_item in answers_payload:
        question_id = str(payload_item["question_id"])
        question = questions.get(question_id)
        if question is None:
            raise ValidationError({"answers": ["Unknown question for this step."]})
        if question_id in candidates:
            raise ValidationError({"answers": ["Duplicate question in payload."]})
        candidates[question_id] = payload_item

    existing = {
        str(answer.question_id): _snapshot_from_model(answer)
        for answer in AssessmentAnswer.objects.filter(assessment=assessment)
        .exclude(question__step=step)
        .select_related("question")
    }

    effective: dict[str, AnswerSnapshot] = dict(existing)
    accepted: dict[str, AnswerSnapshot] = {}
    # Process in configured order so a show_if rule can reference an earlier
    # question in the same step. Hidden answers are discarded.
    for question in step.questions.all():
        question_id = str(question.id)
        if not _question_visible(question, effective):
            continue
        answer_payload = candidates.get(question_id)
        if answer_payload is None:
            continue
        snapshot = _validate_answer(question, answer_payload)
        if _has_answer(question, snapshot):
            accepted[question_id] = snapshot
            effective[question_id] = snapshot

    for question in visible_questions(step, effective):
        if question.required and not _has_answer(question, effective.get(str(question.id))):
            raise ValidationError({"answers": [f"{question.key} is required."]})

    with transaction.atomic():
        AssessmentAnswer.objects.filter(assessment=assessment, question__step=step).delete()
        AssessmentAnswer.objects.bulk_create(
            [
                AssessmentAnswer(
                    assessment=assessment,
                    question=questions[question_id],
                    option_ids=list(snapshot.option_ids),
                    answer=snapshot.answer,
                    text=snapshot.text,
                )
                for question_id, snapshot in accepted.items()
            ]
        )
        refresh_screening(assessment)

    return step, accepted, step_is_complete(assessment, step, effective)


def step_answers_payload(assessment: Assessment, step: QuestionnaireStep) -> dict[str, Any]:
    snapshots = _snapshots_for(assessment)
    answers: list[dict[str, Any]] = []
    for question in visible_questions(step, snapshots):
        snapshot = snapshots.get(str(question.id))
        if snapshot is None or not _has_answer(question, snapshot):
            continue
        item: dict[str, Any] = {"question_id": str(question.id)}
        if question.type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE):
            item["option_ids"] = list(snapshot.option_ids)
        elif question.type == QuestionType.YES_NO:
            item["answer"] = snapshot.answer
        else:
            item["text"] = snapshot.text
        answers.append(item)
    return {
        "step_key": step.key,
        "answers": answers,
        "is_complete": step_is_complete(assessment, step, snapshots),
    }


def all_step_answers(assessment: Assessment) -> list[dict[str, Any]]:
    return [
        step_answers_payload(assessment, step)
        for step in assessment.questionnaire_version.steps.all()
        if step.type == StepType.QUESTIONS
    ]


def summary_data(assessment: Assessment) -> dict[str, Any]:
    progress = assessment_progress(assessment)
    if not progress.is_complete:
        raise AssessmentIncomplete(details={"missing_steps": progress.missing_step_keys})
    measurement = AssessmentMeasurement.objects.filter(assessment=assessment).first()
    if measurement is None:
        raise AssessmentIncomplete(details={"missing_steps": progress.missing_step_keys})
    program = assessment.program
    result = compute_screening(assessment) or ScreeningResult.ELIGIBLE
    projection, timeline_weeks = projection_for(program, measurement)
    return {
        "current_weight": display_weight(
            measurement.current_weight_kg, assessment.display_weight_unit
        ),
        "target_weight": display_weight(
            measurement.target_weight_kg, assessment.display_weight_unit
        ),
        "bmi": float(measurement.bmi),
        "bmi_category": measurement.bmi_category,
        "bmi_scale": BMI_SCALE,
        "weight_goal_kg": float(measurement.weight_goal_kg),
        "estimated_timeline_weeks": timeline_weeks,
        "projection": projection,
        "screening_result": result,
        "screening_message": screening_message(program, result),
    }


def screening_message(program: Program, result: str) -> str:
    messages: dict[str, str] = {
        ScreeningResult.ELIGIBLE: program.screening_message_eligible,
        ScreeningResult.NEEDS_REVIEW: program.screening_message_needs_review,
        ScreeningResult.NOT_ELIGIBLE: program.screening_message_not_eligible,
    }
    return messages.get(result, program.screening_message_eligible)


def projection_for(
    program: Program, measurement: AssessmentMeasurement
) -> tuple[dict[str, Any], int]:
    """Illustrative projection (Part 2, 3.8) and the estimated timeline in weeks."""

    percents = program.projection_loss_percent_by_month
    if isinstance(percents, list) and percents:
        return _percent_projection(program, measurement, [Decimal(str(p)) for p in percents])
    weekly = program.projection_weekly_loss_kg or Decimal("0")
    goal = measurement.weight_goal_kg
    timeline_weeks = max(1, math.ceil(float(goal / weekly))) if weekly > 0 and goal > 0 else 0
    monthly = weekly * Decimal("4.345")
    current = measurement.current_weight_kg
    target = measurement.target_weight_kg
    points: list[dict[str, Any]] = []
    last = current
    for month in range(1, program.projection_months + 1):
        projected = max(target, current - monthly * month)
        last = projected
        points.append({"month": month, "weight_kg": float(_q(projected, 2))})
    projection = {
        "months": program.projection_months,
        "projected_loss_kg": float(_q(current - last, 2)),
        "points": points,
        "disclaimer": program.projection_disclaimer,
    }
    return projection, timeline_weeks


def _percent_projection(
    program: Program, measurement: AssessmentMeasurement, percents: list[Decimal]
) -> tuple[dict[str, Any], int]:
    """Projection from admin-configured cumulative loss percentages (Part 5, 8.2)."""

    current = measurement.current_weight_kg
    target = measurement.target_weight_kg
    points: list[dict[str, Any]] = []
    last = current
    reached_month = 0
    for month, percent in enumerate(percents, start=1):
        projected = max(target, current * (Decimal("1") - percent / Decimal("100")))
        last = projected
        if reached_month == 0 and projected <= target:
            reached_month = month
        points.append({"month": month, "weight_kg": float(_q(projected, 2))})
    timeline_weeks = math.ceil((reached_month or len(percents)) * 4.345)
    projection = {
        "months": len(percents),
        "projected_loss_kg": float(_q(current - last, 2)),
        "points": points,
        "disclaimer": program.projection_disclaimer,
    }
    return projection, timeline_weeks


class EvaluationService:
    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        EvaluationStatus.SCREENED_OUT: {
            EvaluationStatus.AWAITING_PAYMENT,
            EvaluationStatus.SUBMITTED,
        },
        EvaluationStatus.AWAITING_PAYMENT: {
            EvaluationStatus.SUBMITTED,
            EvaluationStatus.CANCELLED,
        },
        EvaluationStatus.SUBMITTED: {
            EvaluationStatus.IN_REVIEW,
            EvaluationStatus.PENDING_LABS,
            EvaluationStatus.CANCELLED,
        },
        EvaluationStatus.IN_REVIEW: {
            EvaluationStatus.APPROVED,
            EvaluationStatus.NOT_SUITABLE,
            EvaluationStatus.PENDING_LABS,
            EvaluationStatus.SUBMITTED,
        },
        EvaluationStatus.PENDING_LABS: {
            EvaluationStatus.SUBMITTED,
            EvaluationStatus.CANCELLED,
        },
        EvaluationStatus.APPROVED: set(),
        EvaluationStatus.NOT_SUITABLE: set(),
        EvaluationStatus.CANCELLED: set(),
    }

    @classmethod
    def transitions_to(cls, evaluation: Evaluation, to_status: str) -> bool:
        return to_status in cls.ALLOWED_TRANSITIONS.get(evaluation.status, set())

    @classmethod
    def transition(
        cls,
        evaluation: Evaluation,
        to_status: str,
        *,
        actor: User | None,
        reason: str = "",
    ) -> Evaluation:
        if evaluation.status == to_status:
            return evaluation
        if not cls.transitions_to(evaluation, to_status):
            raise InvalidStateTransition()
        from_status = evaluation.status
        evaluation.status = to_status
        evaluation.version = (evaluation.version or 0) + 1
        update_fields = ["status", "version", "updated_at"]
        if to_status in (EvaluationStatus.APPROVED, EvaluationStatus.NOT_SUITABLE):
            evaluation.decided_at = timezone.now()
            update_fields.append("decided_at")
        evaluation.save(update_fields=update_fields)
        EvaluationStatusHistory.objects.create(
            evaluation=evaluation,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            reason=reason,
        )
        cls._notify_patient(evaluation, to_status, actor)
        if to_status == EvaluationStatus.SUBMITTED and evaluation.physician_id is None:
            from apps.clinical.evaluations import notify_queue

            notify_queue(evaluation)
        return evaluation

    @staticmethod
    def _notify_patient(evaluation: Evaluation, to_status: str, actor: User | None) -> None:
        """In-app + email notice (Part 3, section 9). Generic text only."""

        from apps.notifications.models import NotificationType
        from apps.notifications.services import notify

        patient = evaluation.assessment.user
        if actor is not None and actor.pk == patient.pk:
            return
        notification_type = (
            NotificationType.LABS_REQUESTED
            if to_status == EvaluationStatus.PENDING_LABS
            else NotificationType.EVALUATION_STATUS_CHANGED
        )
        notify(
            patient,
            notification_type,
            target_kind="evaluation",
            target_id=evaluation.assessment_id,
        )


def submit_assessment(assessment: Assessment, *, request: Any) -> tuple[Evaluation, str]:
    if assessment.status != AssessmentStatus.DRAFT:
        raise AssessmentLocked()
    user = assessment.user
    if not user.is_email_verified:
        raise EmailNotVerified()
    if not has_assessment_consents(user):
        raise ConsentRequired()
    progress = assessment_progress(assessment)
    if not progress.is_complete:
        raise AssessmentIncomplete(details={"missing_steps": progress.missing_step_keys})

    with transaction.atomic():
        locked = Assessment.objects.select_for_update().get(pk=assessment.pk)
        if locked.status != AssessmentStatus.DRAFT:
            raise AssessmentLocked()
        result = compute_screening(locked) or ScreeningResult.ELIGIBLE
        program = locked.program
        fee_waived = program.fee_is_waived or program.fee_amount == 0
        if result == ScreeningResult.NOT_ELIGIBLE:
            evaluation_status = EvaluationStatus.SCREENED_OUT
            fee_status = FeeStatus.NOT_REQUIRED
        elif fee_waived:
            evaluation_status = EvaluationStatus.SUBMITTED
            fee_status = FeeStatus.WAIVED if program.fee_is_waived else FeeStatus.NOT_REQUIRED
        else:
            evaluation_status = EvaluationStatus.AWAITING_PAYMENT
            fee_status = FeeStatus.PENDING

        now = timezone.now()
        locked.status = AssessmentStatus.SUBMITTED
        locked.submitted_at = now
        locked.screening_result = result
        locked.save(update_fields=["status", "submitted_at", "screening_result", "updated_at"])
        evaluation = Evaluation.objects.create(
            assessment=locked,
            status=evaluation_status,
            submitted_at=now,
        )
        EvaluationStatusHistory.objects.create(
            evaluation=evaluation,
            from_status="",
            to_status=evaluation_status,
            reason="Assessment submitted",
        )
        EvaluationFee.objects.create(
            evaluation=evaluation,
            status=fee_status,
            amount=program.fee_amount,
            currency=program.fee_currency,
            is_waived=program.fee_is_waived,
            title=program.fee_title,
            inclusions=program.fee_inclusions,
            payment_methods=program.payment_methods,
        )
        record_audit(
            actor=user,
            action="assessment.submitted",
            object_type="Assessment",
            object_id=locked.pk,
            request=request,
        )
        record_audit(
            actor=user,
            action="evaluation.created",
            object_type="Evaluation",
            object_id=evaluation.pk,
            request=request,
            metadata={"status": evaluation_status},
        )
        if evaluation_status == EvaluationStatus.SUBMITTED:
            from apps.clinical.evaluations import notify_queue

            notify_queue(evaluation)
        next_screen = {
            EvaluationStatus.SCREENED_OUT: "SCREENED_OUT_MESSAGE",
            EvaluationStatus.AWAITING_PAYMENT: "PAY_FEE",
        }.get(evaluation_status, "RECOMMENDED_LABS")
        transaction.on_commit(
            lambda: logger.info(
                "assessment.submitted", assessment_id=str(locked.pk), user_id=str(user.pk)
            )
        )
        transaction.on_commit(
            lambda: logger.info(
                "evaluation.created",
                evaluation_id=str(evaluation.pk),
                status=evaluation_status,
            )
        )
    return evaluation, next_screen


def request_review(assessment: Assessment, *, note: str, request: Any) -> EvaluationReviewRequest:
    evaluation = Evaluation.objects.filter(assessment=assessment).first()
    if evaluation is None or evaluation.status != EvaluationStatus.SCREENED_OUT:
        raise InvalidStateTransition()
    from apps.core.exceptions import Conflict

    if EvaluationReviewRequest.objects.filter(evaluation=evaluation).exists():
        raise Conflict("A review has already been requested for this evaluation.")
    review = EvaluationReviewRequest.objects.create(evaluation=evaluation, note=note.strip())
    record_audit(
        actor=assessment.user,
        action="evaluation.review_requested",
        object_type="Evaluation",
        object_id=evaluation.pk,
        request=request,
    )
    return review


def cancel_assessment(assessment: Assessment, *, reason: str, request: Any) -> Assessment:
    if assessment.status == AssessmentStatus.DRAFT:
        assessment.status = AssessmentStatus.CANCELLED
        assessment.cancelled_at = timezone.now()
        assessment.cancellation_reason = reason.strip()
        assessment.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancellation_reason",
                "updated_at",
            ]
        )
    elif assessment.status == AssessmentStatus.SUBMITTED:
        evaluation = Evaluation.objects.filter(assessment=assessment).first()
        if evaluation is None or evaluation.status not in CANCELLABLE_EVALUATION_STATUSES:
            raise InvalidStateTransition()
        with transaction.atomic():
            EvaluationService.transition(
                evaluation,
                EvaluationStatus.CANCELLED,
                actor=assessment.user,
                reason=reason.strip(),
            )
            assessment.status = AssessmentStatus.CANCELLED
            assessment.cancelled_at = timezone.now()
            assessment.cancellation_reason = reason.strip()
            assessment.save(
                update_fields=[
                    "status",
                    "cancelled_at",
                    "cancellation_reason",
                    "updated_at",
                ]
            )
    else:
        raise InvalidStateTransition()
    record_audit(
        actor=assessment.user,
        action="assessment.cancelled",
        object_type="Assessment",
        object_id=assessment.pk,
        request=request,
        metadata={"reason": reason.strip()},
    )
    return assessment


def evaluation_checklist(evaluation: Evaluation) -> list[dict[str, str]]:
    assessment = evaluation.assessment
    labs_state = (
        ChecklistState.DONE
        if assessment.labs_status == LabsStatus.SUBMITTED
        else ChecklistState.PENDING
    )
    if evaluation.status in (EvaluationStatus.APPROVED, EvaluationStatus.NOT_SUITABLE):
        review_state = ChecklistState.DONE
    elif evaluation.status == EvaluationStatus.IN_REVIEW:
        review_state = ChecklistState.ACTIVE
    elif evaluation.status in (
        EvaluationStatus.AWAITING_PAYMENT,
        EvaluationStatus.SCREENED_OUT,
    ):
        review_state = ChecklistState.BLOCKED
    else:
        review_state = ChecklistState.PENDING
    return [
        {
            "key": "QUESTIONNAIRE",
            "label": "Medical questionnaire",
            "state": ChecklistState.DONE,
        },
        {"key": "LABS", "label": "Lab results", "state": labs_state},
        {
            "key": "PHYSICIAN_REVIEW",
            "label": "Physician review",
            "state": review_state,
        },
    ]


def journey_for(assessment: Assessment) -> list[dict[str, str]]:
    evaluation = Evaluation.objects.filter(assessment=assessment).first()
    status = evaluation.status if evaluation is not None else ""
    first = (
        ChecklistState.DONE
        if assessment.status == AssessmentStatus.SUBMITTED
        else ChecklistState.ACTIVE
        if assessment.status == AssessmentStatus.DRAFT
        else ChecklistState.PENDING
    )
    if assessment.labs_status == LabsStatus.SUBMITTED:
        second = ChecklistState.DONE
    elif status in (
        EvaluationStatus.SUBMITTED,
        EvaluationStatus.PENDING_LABS,
        EvaluationStatus.IN_REVIEW,
    ):
        second = ChecklistState.ACTIVE
    elif status in (
        EvaluationStatus.AWAITING_PAYMENT,
        EvaluationStatus.SCREENED_OUT,
    ):
        second = ChecklistState.BLOCKED
    else:
        second = ChecklistState.PENDING
    if status in (EvaluationStatus.APPROVED, EvaluationStatus.NOT_SUITABLE):
        review = ChecklistState.DONE
    elif status == EvaluationStatus.IN_REVIEW:
        review = ChecklistState.ACTIVE
    elif status in (EvaluationStatus.AWAITING_PAYMENT, EvaluationStatus.SCREENED_OUT):
        review = ChecklistState.BLOCKED
    else:
        review = ChecklistState.PENDING
    prescription = (
        ChecklistState.DONE
        if status == EvaluationStatus.APPROVED
        else ChecklistState.BLOCKED
        if status in (EvaluationStatus.NOT_SUITABLE, EvaluationStatus.CANCELLED)
        else ChecklistState.PENDING
    )
    order = (
        ChecklistState.PENDING
        if status == EvaluationStatus.APPROVED
        else ChecklistState.BLOCKED
        if status in (EvaluationStatus.NOT_SUITABLE, EvaluationStatus.CANCELLED)
        else ChecklistState.PENDING
    )
    return [
        {"step": JourneyStep.FIRST_ASSESSMENT, "state": first},
        {"step": JourneyStep.SECOND_ASSESSMENT, "state": second},
        {"step": JourneyStep.ELIGIBILITY_REVIEW, "state": review},
        {"step": JourneyStep.PRESCRIPTION, "state": prescription},
        {"step": JourneyStep.ORDER, "state": order},
    ]


def can_upload_labs(evaluation: Evaluation) -> bool:
    return evaluation.status in LAB_UPLOAD_EVALUATION_STATUSES


def can_set_appointment(evaluation: Evaluation) -> bool:
    return evaluation.status in (
        EvaluationStatus.SUBMITTED,
        EvaluationStatus.IN_REVIEW,
        EvaluationStatus.PENDING_LABS,
        EvaluationStatus.APPROVED,
    )


def lab_recommendations(evaluation: Evaluation) -> dict[str, Any]:
    program = evaluation.assessment.program
    panels = (
        evaluation.lab_recommendations
        if isinstance(evaluation.lab_recommendations, list)
        else program.lab_panels
    )
    return {
        "intro": program.lab_recommendations_intro,
        "group_label": program.lab_group_label,
        "recommended_by": evaluation.lab_recommendations_source,
        "panels": panels,
    }


def evaluation_copy(evaluation: Evaluation) -> dict[str, str]:
    copy = evaluation.assessment.program.evaluation_copy_for(evaluation.status)
    if copy["title"] and copy["message"]:
        return copy
    fallback: dict[str, dict[str, str]] = {
        EvaluationStatus.SCREENED_OUT: {
            "title": "Thank you for completing your assessment",
            "message": "Our support team can help you find the right care path.",
        },
        EvaluationStatus.AWAITING_PAYMENT: {
            "title": "Complete your payment",
            "message": "Pay the evaluation fee to send your assessment to a physician.",
        },
        EvaluationStatus.SUBMITTED: {
            "title": "Your evaluation is under review",
            "message": "We will notify you when your evaluation is complete.",
        },
        EvaluationStatus.IN_REVIEW: {
            "title": "Your evaluation is under review",
            "message": "We will notify you when your evaluation is complete.",
        },
        EvaluationStatus.PENDING_LABS: {
            "title": "Your physician needs lab results",
            "message": "Upload your blood test so the physician can continue.",
        },
        EvaluationStatus.APPROVED: {
            "title": "Your evaluation is complete",
            "message": "Your prescription is ready.",
        },
        EvaluationStatus.NOT_SUITABLE: {
            "title": "Alternative In-Person Care Recommended",
            "message": "We recommend an in-person evaluation.",
        },
        EvaluationStatus.CANCELLED: {
            "title": "Evaluation cancelled",
            "message": "This evaluation is no longer active.",
        },
    }
    return fallback.get(evaluation.status, {"title": "", "message": ""})


__all__ = [
    "ACTIVE_EVALUATION_STATUSES",
    "BMI_SCALE",
    "CANCELLABLE_EVALUATION_STATUSES",
    "LAB_UPLOAD_EVALUATION_STATUSES",
    "EvaluationService",
    "all_step_answers",
    "assessment_progress",
    "calculate_bmi",
    "can_set_appointment",
    "can_upload_labs",
    "cancel_assessment",
    "compute_screening",
    "create_or_resume_assessment",
    "current_step_key",
    "display_height",
    "display_weight",
    "evaluation_checklist",
    "evaluation_copy",
    "has_assessment_consents",
    "height_to_cm",
    "journey_for",
    "lab_recommendations",
    "money",
    "projection_for",
    "refresh_screening",
    "request_review",
    "save_measurements",
    "save_step",
    "screening_message",
    "step_answers_payload",
    "submit_assessment",
    "summary_data",
    "visible_questions",
    "weight_to_kg",
]
