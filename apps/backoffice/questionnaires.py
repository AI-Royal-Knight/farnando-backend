"""Questionnaire editor: drafts, steps, questions, validation and publishing (Part 5, 8.4)."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Count, Max, QuerySet
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.assessments.enums import QuestionType, StepType
from apps.assessments.models import (
    Program,
    QuestionnaireOption,
    QuestionnaireQuestion,
    QuestionnaireStatus,
    QuestionnaireStep,
    QuestionnaireVersion,
)
from apps.assessments.services import money
from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, InvalidStateTransition

CHOICE_TYPES = (QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE)
MEASUREMENTS_KEY = "measurements"


def _audit(actor: User, action: str, questionnaire: QuestionnaireVersion, request: Any) -> None:
    record_audit(
        actor=actor,
        action=action,
        object_type="QuestionnaireVersion",
        object_id=questionnaire.pk,
        request=request,
    )


def queryset() -> QuerySet[QuestionnaireVersion]:
    return QuestionnaireVersion.objects.select_related("program").annotate(
        questions_count=Count("steps__questions", distinct=True)
    )


def get(questionnaire_id: Any) -> QuestionnaireVersion:
    questionnaire = queryset().filter(pk=questionnaire_id).first()
    if questionnaire is None:
        raise NotFound()
    return questionnaire


def _draft(questionnaire: QuestionnaireVersion) -> None:
    if questionnaire.status != QuestionnaireStatus.DRAFT:
        raise InvalidStateTransition("Published questionnaires are immutable. Create a new draft.")


def list_item(questionnaire: QuestionnaireVersion) -> dict[str, Any]:
    program = questionnaire.program
    return {
        "id": questionnaire.pk,
        "program": {"id": program.pk, "name": program.name},
        "title": questionnaire.title,
        "description": questionnaire.description,
        "status": questionnaire.status,
        "version": questionnaire.version,
        "questions_count": getattr(questionnaire, "questions_count", 0),
        "is_active": questionnaire.is_active,
        "fee": None if program.fee_is_waived else money(program.fee_amount, program.fee_currency),
        "updated_at": questionnaire.updated_at,
    }


def question_payload(question: QuestionnaireQuestion) -> dict[str, Any]:
    show_if = None
    if question.show_if_id is not None:
        show_if = {"question_id": question.show_if_id, "equals": list(question.show_if_equals)}
    return {
        "id": question.pk,
        "key": question.key,
        "text": question.text,
        "help_text": question.help_text,
        "type": question.type,
        "required": question.required,
        "position": question.position,
        "options": [
            {
                "id": option.pk,
                "label": option.label,
                "is_exclusive": option.is_exclusive,
                "screening_effect": option.screening_effect,
            }
            for option in question.options.all()
        ],
        "screening_effect": question.screening_effect,
        "yes_effect": question.yes_effect,
        "no_effect": question.no_effect,
        "show_if": show_if,
        "review_group": question.review_group,
        "placeholder": question.placeholder,
        "max_length": question.max_length,
    }


def detail(questionnaire: QuestionnaireVersion) -> dict[str, Any]:
    body = list_item(questionnaire)
    steps = questionnaire.steps.prefetch_related("questions__options").order_by("position")
    body["publish_note"] = questionnaire.publish_note or None
    body["published_at"] = questionnaire.published_at
    body["steps"] = [
        {
            "id": step.pk,
            "key": step.key,
            "title": step.title,
            "subtitle": step.subtitle,
            "type": step.type,
            "position": step.position,
            "questions": [question_payload(question) for question in step.questions.all()],
        }
        for step in steps
    ]
    return body


def _has_draft(program: Program) -> bool:
    return program.questionnaires.filter(status=QuestionnaireStatus.DRAFT).exists()


def _next_version(program: Program) -> int:
    return int(program.questionnaires.aggregate(top=Max("version"))["top"] or 0) + 1


def create(*, data: dict[str, Any], actor: User, request: Any) -> QuestionnaireVersion:
    program = Program.objects.filter(pk=data["program_id"]).first()
    if program is None:
        raise ValidationError({"program_id": ["Unknown program."]})
    with transaction.atomic():
        Program.objects.select_for_update().get(pk=program.pk)
        if _has_draft(program):
            raise Conflict("This program already has a draft questionnaire.")
        questionnaire = QuestionnaireVersion.objects.create(
            program=program,
            version=_next_version(program),
            title=data["title"],
            description=data.get("description", ""),
            status=QuestionnaireStatus.DRAFT,
            created_by=actor,
        )
        QuestionnaireStep.objects.create(
            questionnaire=questionnaire,
            key=MEASUREMENTS_KEY,
            title="Your measurements",
            type=StepType.MEASUREMENTS,
            position=0,
        )
        _audit(actor, "questionnaire.created", questionnaire, request)
    return get(questionnaire.pk)


def new_draft(
    *, questionnaire: QuestionnaireVersion, actor: User, request: Any
) -> QuestionnaireVersion:
    program = questionnaire.program
    with transaction.atomic():
        Program.objects.select_for_update().get(pk=program.pk)
        if _has_draft(program):
            raise Conflict("This program already has a draft questionnaire.")
        source = program.questionnaires.filter(status=QuestionnaireStatus.PUBLISHED).first()
        source = source or questionnaire
        clone = QuestionnaireVersion.objects.create(
            program=program,
            version=_next_version(program),
            title=source.title,
            description=source.description,
            status=QuestionnaireStatus.DRAFT,
            created_by=actor,
        )
        question_map: dict[Any, QuestionnaireQuestion] = {}
        option_map: dict[str, str] = {}
        pending_show_if: list[tuple[QuestionnaireQuestion, Any, list[Any]]] = []
        for step in source.steps.prefetch_related("questions__options").order_by("position"):
            new_step = QuestionnaireStep.objects.create(
                questionnaire=clone,
                key=step.key,
                title=step.title,
                subtitle=step.subtitle,
                type=step.type,
                position=step.position,
            )
            for question in step.questions.all():
                old_id = question.pk
                new_question = QuestionnaireQuestion.objects.create(
                    step=new_step,
                    key=question.key,
                    text=question.text,
                    help_text=question.help_text,
                    type=question.type,
                    required=question.required,
                    position=question.position,
                    max_length=question.max_length,
                    placeholder=question.placeholder,
                    screening_effect=question.screening_effect,
                    yes_effect=question.yes_effect,
                    no_effect=question.no_effect,
                    review_group=question.review_group,
                )
                question_map[old_id] = new_question
                for option in question.options.all():
                    new_option = QuestionnaireOption.objects.create(
                        question=new_question,
                        label=option.label,
                        position=option.position,
                        is_exclusive=option.is_exclusive,
                        screening_effect=option.screening_effect,
                    )
                    option_map[str(option.pk)] = str(new_option.pk)
                if question.show_if_id is not None:
                    pending_show_if.append(
                        (new_question, question.show_if_id, list(question.show_if_equals))
                    )
        for new_question, old_parent, equals in pending_show_if:
            parent = question_map.get(old_parent)
            if parent is not None:
                new_question.show_if = parent
                new_question.show_if_equals = [option_map.get(str(v), v) for v in equals]
                new_question.save(update_fields=["show_if", "show_if_equals"])
        _audit(actor, "questionnaire.created", clone, request)
    return get(clone.pk)


def update(
    *, questionnaire: QuestionnaireVersion, data: dict[str, Any], actor: User, request: Any
) -> QuestionnaireVersion:
    _draft(questionnaire)
    for field in ("title", "description"):
        if field in data:
            setattr(questionnaire, field, data[field])
    questionnaire.save()
    _audit(actor, "questionnaire.updated", questionnaire, request)
    return get(questionnaire.pk)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def get_step(step_id: Any) -> QuestionnaireStep:
    step = QuestionnaireStep.objects.select_related("questionnaire").filter(pk=step_id).first()
    if step is None:
        raise NotFound()
    return step


def add_step(
    *, questionnaire: QuestionnaireVersion, data: dict[str, Any], actor: User, request: Any
) -> QuestionnaireStep:
    _draft(questionnaire)
    if questionnaire.steps.filter(key=data["key"]).exists():
        raise ValidationError({"key": ["Already used in this questionnaire."]})
    top = questionnaire.steps.aggregate(top=Max("position"))["top"] or 0
    step = QuestionnaireStep.objects.create(
        questionnaire=questionnaire,
        key=data["key"],
        title=data["title"],
        subtitle=data.get("subtitle", ""),
        type=StepType.QUESTIONS,
        position=top + 1,
    )
    _audit(actor, "questionnaire.updated", questionnaire, request)
    return step


def update_step(
    *, step: QuestionnaireStep, data: dict[str, Any], actor: User, request: Any
) -> QuestionnaireStep:
    _draft(step.questionnaire)
    if "key" in data and data["key"] != step.key:
        if step.type == StepType.MEASUREMENTS:
            raise ValidationError({"key": ["The measurements step key is fixed."]})
        if step.questionnaire.steps.filter(key=data["key"]).exclude(pk=step.pk).exists():
            raise ValidationError({"key": ["Already used in this questionnaire."]})
    for field in ("title", "subtitle", "key"):
        if field in data:
            setattr(step, field, data[field])
    step.save()
    _audit(actor, "questionnaire.updated", step.questionnaire, request)
    return step


def delete_step(*, step: QuestionnaireStep, actor: User, request: Any) -> None:
    _draft(step.questionnaire)
    if step.type == StepType.MEASUREMENTS:
        raise InvalidStateTransition("The measurements step cannot be deleted.")
    questionnaire = step.questionnaire
    step.delete()
    _audit(actor, "questionnaire.updated", questionnaire, request)


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def get_question(question_id: Any) -> QuestionnaireQuestion:
    question = (
        QuestionnaireQuestion.objects.select_related("step__questionnaire")
        .prefetch_related("options")
        .filter(pk=question_id)
        .first()
    )
    if question is None:
        raise NotFound()
    return question


def _ordered_questions(questionnaire: QuestionnaireVersion) -> list[QuestionnaireQuestion]:
    return list(
        QuestionnaireQuestion.objects.filter(step__questionnaire=questionnaire).order_by(
            "step__position", "position"
        )
    )


def _validate_question_body(
    data: dict[str, Any], *, question_type: str, field_prefix: str = ""
) -> None:
    options = data.get("options")
    if question_type in CHOICE_TYPES:
        if options is None or not 2 <= len(options) <= 12:
            raise ValidationError({f"{field_prefix}options": ["Between 2 and 12 options."]})
        if sum(1 for option in options if option.get("is_exclusive")) > 1:
            raise ValidationError({f"{field_prefix}options": ["At most one exclusive option."]})
    elif options:
        raise ValidationError({f"{field_prefix}options": ["Only choice questions have options."]})
    if question_type == QuestionType.TEXT and not data.get("max_length"):
        raise ValidationError({f"{field_prefix}max_length": ["Required for TEXT questions."]})


def _apply_show_if(question: QuestionnaireQuestion, show_if: dict[str, Any] | None) -> None:
    if show_if is None:
        question.show_if = None
        question.show_if_equals = []
        return
    ordered = _ordered_questions(question.step.questionnaire)
    ids = [str(item.pk) for item in ordered]
    parent_id = str(show_if["question_id"])
    if parent_id not in ids:
        raise ValidationError({"show_if": ["Must reference a question in this questionnaire."]})
    own = str(question.pk)
    if own in ids and ids.index(parent_id) >= ids.index(own):
        raise ValidationError({"show_if": ["Must reference an earlier question."]})
    question.show_if_id = show_if["question_id"]
    question.show_if_equals = [str(value) for value in show_if["equals"]]


def _replace_options(question: QuestionnaireQuestion, options: list[dict[str, Any]]) -> None:
    question.options.all().delete()
    QuestionnaireOption.objects.bulk_create(
        [
            QuestionnaireOption(
                question=question,
                label=option["label"],
                position=index,
                is_exclusive=bool(option.get("is_exclusive")),
                screening_effect=option.get("screening_effect") or "NONE",
            )
            for index, option in enumerate(options)
        ]
    )


def _next_key(questionnaire: QuestionnaireVersion) -> str:
    used = set(
        QuestionnaireQuestion.objects.filter(step__questionnaire=questionnaire).values_list(
            "key", flat=True
        )
    )
    index = len(used) + 1
    while f"q{index:02d}" in used:
        index += 1
    return f"q{index:02d}"


QUESTION_FIELDS = ("text", "help_text", "type", "required", "review_group", "placeholder")


def add_question(
    *, step: QuestionnaireStep, data: dict[str, Any], actor: User, request: Any
) -> QuestionnaireQuestion:
    questionnaire = step.questionnaire
    _draft(questionnaire)
    if step.type != StepType.QUESTIONS:
        raise InvalidStateTransition("Questions belong to question steps.")
    _validate_question_body(data, question_type=data["type"])
    with transaction.atomic():
        top = step.questions.aggregate(top=Max("position"))["top"]
        question = QuestionnaireQuestion(
            step=step,
            key=_next_key(questionnaire),
            position=(top + 1) if top is not None else 0,
            max_length=data.get("max_length"),
        )
        for field in QUESTION_FIELDS:
            if field in data:
                setattr(question, field, data[field])
        question.save()
        if data.get("show_if"):
            _apply_show_if(question, data["show_if"])
            question.save()
        if data.get("options"):
            _replace_options(question, data["options"])
        _audit(actor, "questionnaire.updated", questionnaire, request)
    return get_question(question.pk)


def update_question(
    *, question: QuestionnaireQuestion, data: dict[str, Any], actor: User, request: Any
) -> QuestionnaireQuestion:
    questionnaire = question.step.questionnaire
    _draft(questionnaire)
    question_type = data.get("type", question.type)
    merged = {
        "options": data.get(
            "options",
            [
                {"label": option.label, "is_exclusive": option.is_exclusive}
                for option in question.options.all()
            ],
        ),
        "max_length": data.get("max_length", question.max_length),
    }
    _validate_question_body(merged, question_type=question_type)
    with transaction.atomic():
        for field in QUESTION_FIELDS:
            if field in data:
                setattr(question, field, data[field])
        if "max_length" in data:
            question.max_length = data["max_length"]
        if "show_if" in data:
            _apply_show_if(question, data["show_if"])
        question.save()
        if "options" in data:
            _replace_options(question, data["options"] or [])
        _audit(actor, "questionnaire.updated", questionnaire, request)
    return get_question(question.pk)


def delete_question(*, question: QuestionnaireQuestion, actor: User, request: Any) -> None:
    questionnaire = question.step.questionnaire
    _draft(questionnaire)
    QuestionnaireQuestion.objects.filter(show_if=question).update(show_if=None, show_if_equals=[])
    question.delete()
    _audit(actor, "questionnaire.updated", questionnaire, request)


def reorder(
    *, questionnaire: QuestionnaireVersion, steps: list[dict[str, Any]], actor: User, request: Any
) -> QuestionnaireVersion:
    _draft(questionnaire)
    step_ids = {str(pk) for pk in questionnaire.steps.values_list("pk", flat=True)}
    question_ids = {
        str(pk)
        for pk in QuestionnaireQuestion.objects.filter(
            step__questionnaire=questionnaire
        ).values_list("pk", flat=True)
    }
    sent_steps = [str(item["id"]) for item in steps]
    sent_questions = [str(q) for item in steps for q in item["question_ids"]]
    if set(sent_steps) != step_ids or len(sent_steps) != len(step_ids):
        raise ValidationError({"steps": ["List every step exactly once."]})
    if set(sent_questions) != question_ids or len(sent_questions) != len(question_ids):
        raise ValidationError({"steps": ["List every question exactly once."]})
    with transaction.atomic():
        for step_position, item in enumerate(steps):
            step = questionnaire.steps.get(pk=item["id"])
            if step.type == StepType.MEASUREMENTS and item["question_ids"]:
                raise ValidationError({"steps": ["The measurements step has no questions."]})
            QuestionnaireStep.objects.filter(pk=step.pk).update(position=step_position)
            for position, question_id in enumerate(item["question_ids"]):
                QuestionnaireQuestion.objects.filter(pk=question_id).update(
                    step=step, position=position
                )
        _audit(actor, "questionnaire.updated", questionnaire, request)
    return get(questionnaire.pk)


def validate(questionnaire: QuestionnaireVersion) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    steps = list(questionnaire.steps.prefetch_related("questions__options").order_by("position"))
    if not any(step.type == StepType.MEASUREMENTS for step in steps):
        errors.append({"path": "steps", "message": "The measurements step is missing."})
    question_steps = [step for step in steps if step.type == StepType.QUESTIONS]
    if not question_steps:
        errors.append({"path": "steps", "message": "Add at least one question step."})
    order: list[str] = []
    for step in steps:
        order.extend(str(question.pk) for question in step.questions.all())
    for step in question_steps:
        questions = list(step.questions.all())
        if not questions:
            errors.append({"path": f"steps.{step.key}", "message": "The step has no questions."})
        for question in questions:
            path = f"steps.{step.key}.{question.key}"
            options = list(question.options.all())
            if question.type in CHOICE_TYPES:
                if not 2 <= len(options) <= 12:
                    errors.append({"path": path, "message": "Between 2 and 12 options."})
                if sum(1 for option in options if option.is_exclusive) > 1:
                    errors.append({"path": path, "message": "At most one exclusive option."})
            if question.type == QuestionType.TEXT and not question.max_length:
                errors.append({"path": path, "message": "TEXT questions need max_length."})
            if question.show_if_id is not None:
                parent = str(question.show_if_id)
                if parent not in order or order.index(parent) >= order.index(str(question.pk)):
                    errors.append({"path": path, "message": "show_if must be an earlier question."})
    return errors


def publish(
    *, questionnaire: QuestionnaireVersion, note: str, actor: User, request: Any
) -> QuestionnaireVersion:
    _draft(questionnaire)
    errors = validate(questionnaire)
    if errors:
        raise ValidationError({"questionnaire": errors})
    with transaction.atomic():
        QuestionnaireVersion.objects.filter(
            program=questionnaire.program, status=QuestionnaireStatus.PUBLISHED
        ).update(status=QuestionnaireStatus.ARCHIVED, is_published=False)
        questionnaire.status = QuestionnaireStatus.PUBLISHED
        questionnaire.is_published = True
        questionnaire.is_active = True
        questionnaire.published_at = timezone.now()
        questionnaire.publish_note = note
        questionnaire.save()
        _audit(actor, "questionnaire.published", questionnaire, request)
    return get(questionnaire.pk)


def set_active(
    *, questionnaire: QuestionnaireVersion, active: bool, actor: User, request: Any
) -> QuestionnaireVersion:
    questionnaire.is_active = active
    questionnaire.save(update_fields=["is_active", "updated_at"])
    _audit(actor, "questionnaire.updated", questionnaire, request)
    return get(questionnaire.pk)
