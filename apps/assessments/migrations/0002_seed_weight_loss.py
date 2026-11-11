"""Seed the Weight Loss programme and its pinned questionnaire.

The questionnaire mirrors Contract Part 2 section 3.5. Screening effects are
proposals for medical review and are editable in the admin; they are never
exposed by a patient endpoint.
"""

from __future__ import annotations

from django.db import migrations
from django.utils import timezone

PROGRAM_SLUG = "weight-loss"

MEASUREMENTS = {
    "key": "measurements",
    "title": "Your Body Measurements",
    "subtitle": (
        "Please provide accurate baseline biometric details to help calculate "
        "your medical BMI and tailor your dosage protocol."
    ),
    "type": "MEASUREMENTS",
    "position": 1,
    "questions": [],
}

MEDICAL_CONDITIONS = {
    "key": "medical-conditions",
    "title": "Conditions and Routine Treatments",
    "subtitle": "",
    "type": "QUESTIONS",
    "position": 2,
    "questions": [
        {
            "key": "medical_conditions",
            "text": "Do you have any of the following medical conditions?",
            "type": "MULTI_CHOICE",
            "required": True,
            "position": 1,
            "options": [
                ("Diabetes", False, "NEEDS_REVIEW"),
                ("Hypertension", False, "NEEDS_REVIEW"),
                ("Heart disease", False, "NEEDS_REVIEW"),
                ("None of the above", True, "NONE"),
            ],
        }
    ],
}

HEALTH_CONDITIONS = {
    "key": "health-conditions",
    "title": "Health Conditions",
    "subtitle": "",
    "type": "QUESTIONS",
    "position": 3,
    "questions": [
        {
            "key": "safety_screening",
            "text": "Do any of the following apply to you?",
            "type": "MULTI_CHOICE",
            "required": True,
            "position": 1,
            "options": [
                ("None of the below", True, "NONE"),
                ("Prior bariatric surgery", False, "NEEDS_REVIEW"),
                ("Inflammatory bowel disease (IBD)", False, "NEEDS_REVIEW"),
                (
                    "Bariatric or abdominal/pelvic surgery within the past year",
                    False,
                    "NEEDS_REVIEW",
                ),
                ("Low sodium / SIADH", False, "NOT_ELIGIBLE"),
                ("Current or routine use of opiate medications", False, "NEEDS_REVIEW"),
                ("Personal history of thyroid cancer", False, "NOT_ELIGIBLE"),
                ("Other thyroid diseases", False, "NEEDS_REVIEW"),
                ("History of active, suspected, or prior cancer", False, "NOT_ELIGIBLE"),
                ("Binge drinking", False, "NEEDS_REVIEW"),
            ],
        },
        {
            "key": "takes_medications",
            "text": "Are you currently taking any medications?",
            "type": "YES_NO",
            "required": True,
            "position": 2,
            "options": [],
            "yes_effect": "NONE",
            "no_effect": "NONE",
        },
        {
            "key": "medication_list",
            "text": "List your current medications",
            "type": "TEXT",
            "required": True,
            "position": 3,
            "options": [],
            "show_if": "takes_medications",
            "show_if_equals": ["YES"],
            "max_length": 1000,
            "placeholder": (
                "Type here... (e.g. Metformin 500mg daily, Lisinopril 10mg)"
            ),
        },
    ],
}


def _create_question(QuestionnaireQuestion, QuestionnaireOption, step, config):
    question = QuestionnaireQuestion.objects.create(
        step=step,
        key=config["key"],
        text=config["text"],
        help_text=config.get("help_text", ""),
        type=config["type"],
        required=config["required"],
        position=config["position"],
        max_length=config.get("max_length"),
        placeholder=config.get("placeholder", ""),
        screening_effect=config.get("screening_effect", "NONE"),
        yes_effect=config.get("yes_effect", "NONE"),
        no_effect=config.get("no_effect", "NONE"),
    )
    for position, (label, is_exclusive, effect) in enumerate(config["options"], start=1):
        QuestionnaireOption.objects.create(
            question=question,
            label=label,
            position=position,
            is_exclusive=is_exclusive,
            screening_effect=effect,
        )
    return question


def seed(apps, schema_editor):
    Program = apps.get_model("assessments", "Program")
    QuestionnaireVersion = apps.get_model("assessments", "QuestionnaireVersion")
    QuestionnaireStep = apps.get_model("assessments", "QuestionnaireStep")
    QuestionnaireQuestion = apps.get_model("assessments", "QuestionnaireQuestion")
    QuestionnaireOption = apps.get_model("assessments", "QuestionnaireOption")

    now = timezone.now()
    program, _created = Program.objects.get_or_create(
        slug=PROGRAM_SLUG,
        defaults={
            "name": "Weight Loss",
            "description": (
                "Doctor-supervised GLP-1 / GIP therapies (Semaglutide and "
                "Tirzepatide) for sustainable fat loss and insulin balance"
            ),
            "icon": "weight-loss",
            "position": 1,
            "is_active": True,
        },
    )
    if QuestionnaireVersion.objects.filter(program=program, version=1).exists():
        return

    questionnaire = QuestionnaireVersion.objects.create(
        program=program,
        version=1,
        is_published=True,
        published_at=now,
    )

    questions_by_key = {}
    for step_config in (MEASUREMENTS, MEDICAL_CONDITIONS, HEALTH_CONDITIONS):
        step = QuestionnaireStep.objects.create(
            questionnaire=questionnaire,
            key=step_config["key"],
            title=step_config["title"],
            subtitle=step_config["subtitle"],
            type=step_config["type"],
            position=step_config["position"],
        )
        for question_config in step_config["questions"]:
            question = _create_question(
                QuestionnaireQuestion, QuestionnaireOption, step, question_config
            )
            questions_by_key[question.key] = question

    medication_list = questions_by_key["medication_list"]
    medication_list.show_if = questions_by_key["takes_medications"]
    medication_list.show_if_equals = ["YES"]
    medication_list.save(update_fields=["show_if", "show_if_equals"])


def unseed(apps, schema_editor):
    Program = apps.get_model("assessments", "Program")
    Program.objects.filter(slug=PROGRAM_SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
