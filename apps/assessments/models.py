"""Programs, questionnaires, assessments, evaluations, fees and lab uploads."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import models

from apps.assessments.enums import (
    AssessmentStatus,
    BmiCategory,
    EvaluationStatus,
    FeeStatus,
    HeightUnit,
    LabRejectionReason,
    LabsStatus,
    LabUploadStatus,
    PaymentPurpose,
    PaymentStatus,
    QuestionType,
    RejectionReasonCode,
    ReviewGroup,
    ScreeningEffect,
    ScreeningResult,
    Sex,
    StepType,
    WeightUnit,
)
from apps.core.encryption import EncryptedTextField
from apps.core.models import BaseModel


def _fee_inclusions() -> list[str]:
    return [
        "Medical questionnaire review",
        "Personalized treatment plan",
        "Baseline lab recommendations",
        "Physician support",
    ]


def _payment_methods() -> list[dict[str, str]]:
    return [{"code": "CULQI", "label": "Culqi"}]


def _lab_panels() -> list[dict[str, str]]:
    return [
        {
            "code": "CMP",
            "name": "Comprehensive Metabolic Panel",
            "description": "Evaluates baseline kidney and liver health for safe dosage titration",
        },
        {
            "code": "HBA1C",
            "name": "Hemoglobin A1c",
            "description": "Measures 90-day average blood glucose and insulin sensitivity",
        },
        {
            "code": "INSULIN",
            "name": "Fasting Insulin",
            "description": "Assesses insulin resistance and cellular metabolic response",
        },
        {
            "code": "LIPID",
            "name": "Lipid Panel",
            "description": "Measures cholesterol, HDL, LDL, and triglyceride balance",
        },
    ]


def _evaluation_copy() -> dict[str, Any]:
    return {
        "SCREENED_OUT": {
            "title": "Thank you for completing your assessment",
            "message": "Our support team can help you find the right care path.",
        },
        "AWAITING_PAYMENT": {
            "title": "Complete your payment",
            "message": "Pay the evaluation fee to send your assessment to a physician.",
        },
        "SUBMITTED": {
            "title": "Your evaluation is under review",
            "message": (
                "We will notify you by email and inside the platform as soon as "
                "your evaluation is complete."
            ),
        },
        "IN_REVIEW": {
            "title": "Your evaluation is under review",
            "message": (
                "We will notify you by email and inside the platform as soon as "
                "your evaluation is complete."
            ),
        },
        "PENDING_LABS": {
            "title": "Your physician needs lab results",
            "message": "Upload your blood test so the physician can continue the review.",
        },
        "APPROVED": {
            "title": "Your evaluation is complete",
            "message": "Your prescription is ready.",
        },
        "NOT_SUITABLE": {
            "title": "Alternative In-Person Care Recommended",
            "message": (
                "Based on your answers, our physicians recommend an in-person "
                "evaluation before starting this treatment."
            ),
        },
        "CANCELLED": {
            "title": "Evaluation cancelled",
            "message": "This evaluation is no longer active.",
        },
    }


def _lifestyle_essentials() -> list[dict[str, str]]:
    return [
        {
            "title": "High Protein",
            "body": "Aim for protein at every meal to protect lean muscle.",
        },
        {
            "title": "Hydration",
            "body": "Drink water through the day, especially after each dose.",
        },
        {
            "title": "Movement",
            "body": "Add light daily activity such as a 20-minute walk.",
        },
    ]


class Program(BaseModel):
    """A treatment programme a patient can be assessed for."""

    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=64, blank=True, default="")
    position = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    # Assessment rules. These are admin-editable configuration, not clinical
    # constants baked into code. Defaults are proposals and marked for review.
    requires_target_lower = models.BooleanField(default=True)
    min_bmi = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    min_bmi_effect = models.CharField(
        max_length=16, choices=ScreeningEffect.choices, default=ScreeningEffect.NOT_ELIGIBLE
    )
    age_min = models.PositiveSmallIntegerField(default=18)
    age_max = models.PositiveSmallIntegerField(default=75)
    not_suitable_cooldown_days = models.PositiveIntegerField(default=30)

    # Evaluation fee configuration.
    fee_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("25.00"))
    fee_currency = models.CharField(max_length=3, default="USD")
    fee_is_waived = models.BooleanField(default=False)
    fee_title = models.CharField(max_length=160, default="What does your evaluation include?")
    fee_inclusions = models.JSONField(default=_fee_inclusions)
    payment_methods = models.JSONField(default=_payment_methods)

    # Projection and copy are configuration so a physician can tune them.
    projection_months = models.PositiveSmallIntegerField(default=3)
    projection_weekly_loss_kg = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("1.00")
    )
    projection_disclaimer = models.TextField(default="Results are not guaranteed and may vary.")
    estimated_review_hours_min = models.PositiveSmallIntegerField(default=24)
    estimated_review_hours_max = models.PositiveSmallIntegerField(default=48)
    screening_message_eligible = models.TextField(
        default="Based on your answers, you may qualify for this program."
    )
    screening_message_needs_review = models.TextField(
        default="Your answers need a physician review before we can confirm eligibility."
    )
    screening_message_not_eligible = models.TextField(
        default="This program may not be the right fit. Our support team can help."
    )
    evaluation_copy = models.JSONField(default=_evaluation_copy)
    not_suitable_title = models.CharField(
        max_length=160, default="Alternative In-Person Care Recommended"
    )
    not_suitable_message = models.TextField(
        default=(
            "Based on your answers, our physicians recommend an in-person "
            "evaluation before starting this treatment."
        )
    )
    support_contact_label = models.CharField(max_length=80, default="Talk to our support team")

    # Lab recommendations.
    lab_recommendations_intro = models.TextField(
        default=(
            "Your physician recommends these baseline lab tests to safely "
            "personalize your treatment protocol."
        )
    )
    lab_group_label = models.CharField(max_length=120, default="Metabolic Care")
    lab_panels = models.JSONField(default=_lab_panels)
    allowed_specialties = models.ManyToManyField(
        "clinical.Specialty",
        blank=True,
        related_name="programs",
        help_text="[OPEN] Empty = every verified physician sees the queue.",
    )

    # Part 3 portal content (admin-editable).
    health_summary_subheadline = models.CharField(
        max_length=200,
        default="Your projected progress over the next {months} months.",
        help_text="Supports {months} and {weeks} placeholders.",
    )
    lifestyle_essentials = models.JSONField(default=_lifestyle_essentials)

    class Meta:
        ordering = ["position", "name"]
        indexes = [models.Index(fields=["is_active", "position"])]

    def __str__(self) -> str:
        return self.name

    @property
    def is_available(self) -> bool:
        return self.questionnaires.filter(is_published=True).exists()

    @property
    def published_questionnaire(self) -> QuestionnaireVersion | None:
        return self.questionnaires.filter(is_published=True).order_by("-version").first()

    def evaluation_copy_for(self, status: str) -> dict[str, str]:
        copy = self.evaluation_copy if isinstance(self.evaluation_copy, dict) else {}
        item = copy.get(status)
        if isinstance(item, dict):
            return {
                "title": str(item.get("title", "")),
                "message": str(item.get("message", "")),
            }
        return {"title": "", "message": ""}


class QuestionnaireVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="questionnaires")
    version = models.PositiveIntegerField()
    is_published = models.BooleanField(default=False, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["program", "version"], name="assessments_questionnaire_version_unique"
            ),
            models.UniqueConstraint(
                fields=["program"],
                condition=models.Q(is_published=True),
                name="assessments_one_published_questionnaire",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.program.slug}@v{self.version}"


class QuestionnaireStep(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    questionnaire = models.ForeignKey(
        QuestionnaireVersion, on_delete=models.CASCADE, related_name="steps"
    )
    key = models.CharField(max_length=64)
    title = models.CharField(max_length=200)
    subtitle = models.TextField(blank=True, default="")
    type = models.CharField(max_length=16, choices=StepType.choices)
    position = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["questionnaire", "key"], name="assessments_step_key_unique"
            )
        ]

    def __str__(self) -> str:
        return self.key


class QuestionnaireQuestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    step = models.ForeignKey(QuestionnaireStep, on_delete=models.CASCADE, related_name="questions")
    key = models.CharField(max_length=80)
    text = models.TextField()
    help_text = models.TextField(blank=True, default="")
    type = models.CharField(max_length=16, choices=QuestionType.choices)
    required = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0, db_index=True)
    show_if = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dependent_questions",
    )
    show_if_equals = models.JSONField(default=list)
    max_length = models.PositiveIntegerField(null=True, blank=True)
    placeholder = models.CharField(max_length=255, blank=True, default="")
    # Admin-only. Options carry the effect for choice questions; these fields
    # support YES/NO and free text without exposing the effect to patients.
    screening_effect = models.CharField(
        max_length=16, choices=ScreeningEffect.choices, default=ScreeningEffect.NONE
    )
    yes_effect = models.CharField(
        max_length=16, choices=ScreeningEffect.choices, default=ScreeningEffect.NONE
    )
    no_effect = models.CharField(
        max_length=16, choices=ScreeningEffect.choices, default=ScreeningEffect.NONE
    )
    review_group = models.CharField(
        max_length=32,
        choices=ReviewGroup.choices,
        default=ReviewGroup.OTHER,
        help_text="Admin-only. Groups answers in the physician review packet.",
    )

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["step", "key"], name="assessments_question_key_unique")
        ]

    def __str__(self) -> str:
        return self.key


class QuestionnaireOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(
        QuestionnaireQuestion, on_delete=models.CASCADE, related_name="options"
    )
    label = models.CharField(max_length=200)
    position = models.PositiveIntegerField(default=0, db_index=True)
    is_exclusive = models.BooleanField(default=False)
    screening_effect = models.CharField(
        max_length=16, choices=ScreeningEffect.choices, default=ScreeningEffect.NONE
    )

    class Meta:
        ordering = ["position", "id"]

    def __str__(self) -> str:
        return self.label


class Assessment(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assessments"
    )
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="assessments")
    questionnaire_version = models.ForeignKey(
        QuestionnaireVersion, on_delete=models.PROTECT, related_name="assessments"
    )
    status = models.CharField(
        max_length=16, choices=AssessmentStatus.choices, default=AssessmentStatus.DRAFT
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = EncryptedTextField(blank=True, default="")
    screening_result = models.CharField(  # noqa: DJ001 - null means "not screened yet".
        max_length=16,
        choices=ScreeningResult.choices,
        null=True,
        blank=True,
    )
    labs_status = models.CharField(
        max_length=16, choices=LabsStatus.choices, default=LabsStatus.NOT_STARTED
    )
    display_weight_unit = models.CharField(
        max_length=4, choices=WeightUnit.choices, default=WeightUnit.KG
    )
    display_height_unit = models.CharField(
        max_length=4, choices=HeightUnit.choices, default=HeightUnit.CM
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "program"],
                condition=models.Q(status=AssessmentStatus.DRAFT),
                name="assessments_one_draft_per_program",
            )
        ]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["program", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.program_id}:{self.status}"


class AssessmentMeasurement(BaseModel):
    assessment = models.OneToOneField(
        Assessment, on_delete=models.CASCADE, related_name="measurement"
    )
    current_weight_kg = models.DecimalField(max_digits=6, decimal_places=2)
    target_weight_kg = models.DecimalField(max_digits=6, decimal_places=2)
    height_cm = models.DecimalField(max_digits=5, decimal_places=1)
    sex = models.CharField(max_length=8, choices=Sex.choices)
    date_of_birth = models.DateField()
    bmi = models.DecimalField(max_digits=4, decimal_places=1)
    bmi_category = models.CharField(max_length=16, choices=BmiCategory.choices)
    target_bmi = models.DecimalField(max_digits=4, decimal_places=1)
    weight_goal_kg = models.DecimalField(max_digits=6, decimal_places=2)

    def __str__(self) -> str:
        return f"{self.assessment_id}:{self.current_weight_kg}kg"


class AssessmentAnswer(BaseModel):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(QuestionnaireQuestion, on_delete=models.PROTECT)
    option_ids = models.JSONField(default=list)
    answer = models.CharField(max_length=8, blank=True, default="")
    text = EncryptedTextField(blank=True, default="")

    class Meta:
        ordering = ["question__position", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "question"], name="assessments_answer_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.assessment_id}:{self.question_id}"


class Evaluation(BaseModel):
    assessment = models.OneToOneField(
        Assessment, on_delete=models.CASCADE, related_name="evaluation"
    )
    status = models.CharField(max_length=24, choices=EvaluationStatus.choices, db_index=True)
    physician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluations",
    )
    submitted_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    patient_note = EncryptedTextField(blank=True, default="")
    lab_recommendations = models.JSONField(null=True, blank=True)
    lab_recommendations_source = models.CharField(max_length=16, default="SYSTEM")
    # Part 4: optimistic concurrency, claims and decision records.
    version = models.PositiveIntegerField(default=1)
    claimed_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    internal_note = EncryptedTextField(blank=True, default="")
    rejection_reason_code = models.CharField(
        max_length=48, choices=RejectionReasonCode.choices, blank=True, default=""
    )

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["status", "physician"]),
            models.Index(fields=["physician", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.assessment_id}:{self.status}"


class EvaluationStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evaluation = models.ForeignKey(
        Evaluation, on_delete=models.CASCADE, related_name="status_history"
    )
    from_status = models.CharField(
        max_length=24, choices=EvaluationStatus.choices, blank=True, default=""
    )
    to_status = models.CharField(max_length=24, choices=EvaluationStatus.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evaluation_transitions",
    )
    reason = EncryptedTextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.evaluation_id}:{self.from_status}->{self.to_status}"


class EvaluationFee(BaseModel):
    evaluation = models.OneToOneField(Evaluation, on_delete=models.CASCADE, related_name="fee")
    status = models.CharField(max_length=16, choices=FeeStatus.choices)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3)
    is_waived = models.BooleanField(default=False)
    title = models.CharField(max_length=160)
    inclusions = models.JSONField(default=list)
    payment_methods = models.JSONField(default=list)

    def __str__(self) -> str:
        return f"{self.evaluation_id}:{self.status}"


class Payment(BaseModel):
    """Every provider charge. Part 2 fees and Part 3 orders/renewals share it."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments",
    )
    purpose = models.CharField(
        max_length=24, choices=PaymentPurpose.choices, default=PaymentPurpose.EVALUATION_FEE
    )
    evaluation = models.ForeignKey(
        Evaluation, on_delete=models.CASCADE, null=True, blank=True, related_name="payments"
    )
    fee = models.ForeignKey(
        EvaluationFee, on_delete=models.CASCADE, null=True, blank=True, related_name="payments"
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.CASCADE, null=True, blank=True, related_name="payments"
    )
    subscription = models.ForeignKey(
        "orders.Subscription",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="payments",
    )
    status = models.CharField(max_length=20, choices=PaymentStatus.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    currency = models.CharField(max_length=3)
    provider = models.CharField(max_length=16, default="CULQI")
    provider_payment_id = models.CharField(max_length=120, blank=True, default="", db_index=True)
    failure_code = models.CharField(max_length=120, blank=True, default="")
    action = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["evaluation", "-created_at"]),
            models.Index(fields=["order", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.purpose}:{self.status}"


class EvaluationReviewRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evaluation = models.OneToOneField(
        Evaluation, on_delete=models.CASCADE, related_name="review_request"
    )
    note = EncryptedTextField(blank=True, default="")
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"review:{self.evaluation_id}"


class LabUpload(BaseModel):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="lab_uploads")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="lab_uploads"
    )
    file_name = models.CharField(max_length=150)
    storage_key = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=80)
    size_bytes = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20, choices=LabUploadStatus.choices, default=LabUploadStatus.PENDING_UPLOAD
    )
    rejection_reason = models.CharField(
        max_length=24, choices=LabRejectionReason.choices, blank=True, default=""
    )
    scanned_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assessment", "is_deleted"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.assessment_id}:{self.file_name}"


class Prescription(BaseModel):
    """Minimal prescription summary. Full treatment data lives in Part 3/4."""

    evaluation = models.OneToOneField(
        Evaluation, on_delete=models.CASCADE, related_name="prescription"
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="issued_prescriptions",
    )
    start_date = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    signed_document = models.ForeignKey(
        "clinical.PrescriptionDocument",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    medication_name = models.CharField(max_length=160)
    dosage_label = models.CharField(max_length=160)
    pdf_key = models.CharField(max_length=255, blank=True, default="")
    can_download = models.BooleanField(default=True)
    can_order = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"prescription:{self.evaluation_id}"
