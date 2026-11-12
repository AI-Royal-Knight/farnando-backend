"""Request/response contracts for Contract Part 2."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from rest_framework import serializers

from apps.assessments import services
from apps.assessments.enums import (
    BmiCategory,
    ChecklistState,
    EvaluationStatus,
    HeightUnit,
    JourneyStep,
    LabsStatus,
    PaymentMethod,
    PaymentPurpose,
    ScreeningResult,
    Sex,
    WeightUnit,
    YesNo,
)
from apps.assessments.models import (
    Assessment,
    AssessmentMeasurement,
    Evaluation,
    EvaluationFee,
    LabUpload,
    Payment,
    Program,
    QuestionnaireOption,
    QuestionnaireQuestion,
    QuestionnaireStep,
    QuestionnaireVersion,
)
from apps.core.serializers import StrictSerializer


class ProgramSummarySerializer(serializers.ModelSerializer[Program]):
    class Meta:
        model = Program
        fields = ["id", "name"]
        read_only_fields = fields


class ProgramSerializer(serializers.ModelSerializer[Program]):
    is_available = serializers.SerializerMethodField()
    icon = serializers.SerializerMethodField()

    class Meta:
        model = Program
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "icon",
            "is_available",
            "position",
        ]
        read_only_fields = fields

    def get_is_available(self, obj: Program) -> bool:
        return obj.is_available

    def get_icon(self, obj: Program) -> str | None:
        return obj.icon or None


class QuestionOptionSerializer(serializers.ModelSerializer[QuestionnaireOption]):
    class Meta:
        model = QuestionnaireOption
        fields = ["id", "label", "is_exclusive"]
        read_only_fields = fields


class QuestionSerializer(serializers.ModelSerializer[QuestionnaireQuestion]):
    help_text = serializers.SerializerMethodField()  # type: ignore[assignment]
    options = QuestionOptionSerializer(many=True, read_only=True)
    show_if = serializers.SerializerMethodField()
    placeholder = serializers.SerializerMethodField()

    class Meta:
        model = QuestionnaireQuestion
        fields = [
            "id",
            "key",
            "text",
            "help_text",
            "type",
            "required",
            "options",
            "show_if",
            "max_length",
            "placeholder",
        ]
        read_only_fields = fields

    def get_help_text(self, obj: QuestionnaireQuestion) -> str | None:
        return obj.help_text or None

    def get_placeholder(self, obj: QuestionnaireQuestion) -> str | None:
        return obj.placeholder or None

    def get_show_if(self, obj: QuestionnaireQuestion) -> dict[str, Any] | None:
        if obj.show_if_id is None:
            return None
        equals = obj.show_if_equals if isinstance(obj.show_if_equals, list) else []
        return {"question_id": str(obj.show_if_id), "equals": [str(value) for value in equals]}


class StepSerializer(serializers.ModelSerializer[QuestionnaireStep]):
    subtitle = serializers.SerializerMethodField()
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = QuestionnaireStep
        fields = ["key", "title", "subtitle", "type", "position", "questions"]
        read_only_fields = fields

    def get_subtitle(self, obj: QuestionnaireStep) -> str | None:
        return obj.subtitle or None


class QuestionnaireSerializer(serializers.ModelSerializer[QuestionnaireVersion]):
    version = serializers.IntegerField()
    program = ProgramSummarySerializer(read_only=True)
    steps = StepSerializer(many=True, read_only=True)

    class Meta:
        model = QuestionnaireVersion
        fields = ["version", "program", "steps"]
        read_only_fields = fields


class DisplaySerializer(serializers.Serializer[Any]):
    value = serializers.FloatField()
    unit = serializers.CharField()


class WeightOutputSerializer(serializers.Serializer[Any]):
    kg = serializers.FloatField()
    display = DisplaySerializer()


class HeightOutputSerializer(serializers.Serializer[Any]):
    cm = serializers.FloatField()
    display = DisplaySerializer()


class MeasurementsSerializer(serializers.ModelSerializer[AssessmentMeasurement]):
    current_weight = serializers.SerializerMethodField()
    target_weight = serializers.SerializerMethodField()
    height = serializers.SerializerMethodField()
    age_years = serializers.SerializerMethodField()
    bmi = serializers.FloatField()
    target_bmi = serializers.FloatField()
    weight_goal_kg = serializers.FloatField()

    class Meta:
        model = AssessmentMeasurement
        fields = [
            "current_weight",
            "target_weight",
            "height",
            "sex",
            "date_of_birth",
            "age_years",
            "bmi",
            "bmi_category",
            "target_bmi",
            "weight_goal_kg",
        ]
        read_only_fields = fields

    def get_current_weight(self, obj: AssessmentMeasurement) -> dict[str, Any]:
        return services.display_weight(obj.current_weight_kg, obj.assessment.display_weight_unit)

    def get_target_weight(self, obj: AssessmentMeasurement) -> dict[str, Any]:
        return services.display_weight(obj.target_weight_kg, obj.assessment.display_weight_unit)

    def get_height(self, obj: AssessmentMeasurement) -> dict[str, Any]:
        return services.display_height(obj.height_cm, obj.assessment.display_height_unit)

    def get_age_years(self, obj: AssessmentMeasurement) -> int:
        return services.age_on(obj.date_of_birth)


class BmiScaleItemSerializer(serializers.Serializer[Any]):
    category = serializers.ChoiceField(choices=BmiCategory.choices)
    label = serializers.CharField()  # type: ignore[assignment]
    min = serializers.FloatField(allow_null=True)
    max = serializers.FloatField(allow_null=True)


class ProjectionPointSerializer(serializers.Serializer[Any]):
    month = serializers.IntegerField()
    weight_kg = serializers.FloatField()


class ProjectionSerializer(serializers.Serializer[Any]):
    months = serializers.IntegerField()
    projected_loss_kg = serializers.FloatField()
    points = ProjectionPointSerializer(many=True)
    disclaimer = serializers.CharField()


class SummarySerializer(serializers.Serializer[Any]):
    current_weight = WeightOutputSerializer()
    target_weight = WeightOutputSerializer()
    bmi = serializers.FloatField()
    bmi_category = serializers.ChoiceField(choices=BmiCategory.choices)
    bmi_scale = BmiScaleItemSerializer(many=True)
    weight_goal_kg = serializers.FloatField()
    estimated_timeline_weeks = serializers.IntegerField()
    projection = ProjectionSerializer()
    screening_result = serializers.ChoiceField(choices=ScreeningResult.choices)
    screening_message = serializers.CharField()


class AnswerOutputSerializer(serializers.Serializer[Any]):
    question_id = serializers.CharField()
    option_ids = serializers.ListField(child=serializers.CharField(), required=False)
    answer = serializers.CharField(required=False)
    text = serializers.CharField(required=False)


class StepAnswersSerializer(serializers.Serializer[Any]):
    step_key = serializers.CharField()
    answers = AnswerOutputSerializer(many=True)
    is_complete = serializers.BooleanField()


class MoneySerializer(serializers.Serializer[Any]):
    amount = serializers.CharField()
    currency = serializers.CharField()


class PaymentSerializer(serializers.ModelSerializer[Payment]):
    purpose = serializers.ChoiceField(choices=PaymentPurpose.choices)
    amount = serializers.SerializerMethodField()
    refunded_amount = serializers.SerializerMethodField()
    failure_code = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "status",
            "purpose",
            "amount",
            "refunded_amount",
            "provider",
            "failure_code",
            "action",
            "created_at",
        ]
        read_only_fields = fields

    def get_amount(self, obj: Payment) -> dict[str, str]:
        return services.money(obj.amount, obj.currency)

    def get_refunded_amount(self, obj: Payment) -> dict[str, str]:
        return services.money(obj.refunded_amount, obj.currency)

    def get_failure_code(self, obj: Payment) -> str | None:
        return obj.failure_code or None


class FeeSerializer(serializers.ModelSerializer[EvaluationFee]):
    amount = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()

    class Meta:
        model = EvaluationFee
        fields = [
            "status",
            "amount",
            "is_waived",
            "title",
            "inclusions",
            "payment_methods",
            "payment",
        ]
        read_only_fields = fields

    def get_amount(self, obj: EvaluationFee) -> dict[str, str]:
        return services.money(obj.amount, obj.currency)

    def get_payment(self, obj: EvaluationFee) -> dict[str, Any] | None:
        payment = obj.payments.order_by("-created_at").first()
        return PaymentSerializer(payment).data if payment is not None else None


class ChecklistItemSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    label = serializers.CharField()  # type: ignore[assignment]
    state = serializers.ChoiceField(choices=ChecklistState.choices)


class JourneyItemSerializer(serializers.Serializer[Any]):
    step = serializers.ChoiceField(choices=JourneyStep.choices)
    state = serializers.ChoiceField(choices=ChecklistState.choices)


class PrescriptionSummarySerializer(serializers.Serializer[Any]):
    medication_name = serializers.CharField()
    dosage_label = serializers.CharField()
    can_download = serializers.BooleanField()
    can_order = serializers.BooleanField()


class ReviewingPhysicianSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    specialty = serializers.CharField()


class AlternativeCareSerializer(serializers.Serializer[Any]):
    title = serializers.CharField()
    message = serializers.CharField()
    support_contact_label = serializers.CharField()


class AssessmentSummarySerializer(serializers.ModelSerializer[Assessment]):
    program = ProgramSummarySerializer(read_only=True)
    evaluation_status = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Assessment
        fields = [
            "id",
            "program",
            "status",
            "evaluation_status",
            "screening_result",
            "progress",
            "submitted_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_evaluation_status(self, obj: Assessment) -> str | None:
        evaluation = _evaluation(obj)
        return evaluation.status if evaluation is not None else None

    def get_progress(self, obj: Assessment) -> dict[str, int]:
        progress = services.assessment_progress(obj)
        return {
            "completed_steps": progress.completed_steps,
            "total_steps": progress.total_steps,
        }


class AssessmentDetailSerializer(AssessmentSummarySerializer):
    questionnaire_version = serializers.IntegerField(source="questionnaire_version.version")
    current_step_key = serializers.SerializerMethodField()
    measurements = serializers.SerializerMethodField()
    answers = serializers.SerializerMethodField()
    fee = serializers.SerializerMethodField()
    journey = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

    class Meta(AssessmentSummarySerializer.Meta):
        fields = [
            "id",
            "program",
            "status",
            "created_at",
            "submitted_at",
            "questionnaire_version",
            "current_step_key",
            "progress",
            "measurements",
            "answers",
            "screening_result",
            "fee",
            "labs_status",
            "evaluation_status",
            "journey",
            "can_edit",
        ]
        read_only_fields = fields

    def get_current_step_key(self, obj: Assessment) -> str | None:
        return services.current_step_key(obj)

    def get_measurements(self, obj: Assessment) -> dict[str, Any] | None:
        measurement = _measurement(obj)
        return MeasurementsSerializer(measurement).data if measurement is not None else None

    def get_answers(self, obj: Assessment) -> list[dict[str, Any]]:
        return list(StepAnswersSerializer(services.all_step_answers(obj), many=True).data)

    def get_fee(self, obj: Assessment) -> dict[str, Any] | None:
        evaluation = _evaluation(obj)
        return FeeSerializer(evaluation.fee).data if evaluation is not None else None

    def get_journey(self, obj: Assessment) -> list[dict[str, str]]:
        return list(JourneyItemSerializer(services.journey_for(obj), many=True).data)

    def get_can_edit(self, obj: Assessment) -> bool:
        return obj.status == "DRAFT"


class EvaluationSerializer(serializers.ModelSerializer[Evaluation]):
    checklist = serializers.SerializerMethodField()
    estimated_review_hours = serializers.SerializerMethodField()
    status_title = serializers.SerializerMethodField()
    status_message = serializers.SerializerMethodField()
    can_set_appointment = serializers.SerializerMethodField()
    can_upload_labs = serializers.SerializerMethodField()
    reviewing_physician = serializers.SerializerMethodField()
    patient_note = serializers.SerializerMethodField()
    prescription = serializers.SerializerMethodField()
    alternative_care = serializers.SerializerMethodField()
    fee = serializers.SerializerMethodField()

    class Meta:
        model = Evaluation
        fields = [
            "id",
            "status",
            "submitted_at",
            "decided_at",
            "checklist",
            "estimated_review_hours",
            "status_title",
            "status_message",
            "can_set_appointment",
            "can_upload_labs",
            "reviewing_physician",
            "patient_note",
            "prescription",
            "alternative_care",
            "fee",
        ]
        read_only_fields = fields

    def get_checklist(self, obj: Evaluation) -> list[dict[str, str]]:
        return list(ChecklistItemSerializer(services.evaluation_checklist(obj), many=True).data)

    def get_estimated_review_hours(self, obj: Evaluation) -> dict[str, int]:
        return obj.assessment.program.review_hours()

    def get_status_title(self, obj: Evaluation) -> str:
        return services.evaluation_copy(obj)["title"]

    def get_status_message(self, obj: Evaluation) -> str:
        return services.evaluation_copy(obj)["message"]

    def get_can_set_appointment(self, obj: Evaluation) -> bool:
        return services.can_set_appointment(obj)

    def get_can_upload_labs(self, obj: Evaluation) -> bool:
        return services.can_upload_labs(obj)

    def get_reviewing_physician(self, obj: Evaluation) -> dict[str, Any] | None:
        physician = obj.physician
        if physician is None:
            return None
        return {
            "id": str(physician.pk),
            "name": physician.get_full_name(),
            "specialty": "",
        }

    def get_patient_note(self, obj: Evaluation) -> str | None:
        return obj.patient_note or None

    def get_prescription(self, obj: Evaluation) -> dict[str, Any] | None:
        if obj.status != EvaluationStatus.APPROVED:
            return None
        prescription = getattr(obj, "prescription", None)
        if prescription is None:
            return None
        return PrescriptionSummarySerializer(prescription).data

    def get_alternative_care(self, obj: Evaluation) -> dict[str, Any] | None:
        if obj.status != EvaluationStatus.NOT_SUITABLE:
            return None
        program = obj.assessment.program
        return {
            "title": program.not_suitable_title,
            "message": program.not_suitable_message,
            "support_contact_label": program.support_contact_label,
        }

    def get_fee(self, obj: Evaluation) -> dict[str, Any] | None:
        fee = getattr(obj, "fee", None)
        return FeeSerializer(fee).data if fee is not None else None


class AssessmentCreateSerializer(StrictSerializer):
    program_id = serializers.UUIDField()


class QuantityInputSerializer(StrictSerializer):
    value = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))
    unit = serializers.ChoiceField(choices=WeightUnit.choices)


class HeightQuantityInputSerializer(StrictSerializer):
    value = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))
    unit = serializers.ChoiceField(choices=HeightUnit.choices)


class MeasurementInputSerializer(StrictSerializer):
    current_weight = QuantityInputSerializer()
    target_weight = QuantityInputSerializer()
    height = HeightQuantityInputSerializer()
    sex = serializers.ChoiceField(choices=Sex.choices)
    date_of_birth = serializers.DateField()


class AnswerInputSerializer(StrictSerializer):
    question_id = serializers.UUIDField()
    option_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True
    )
    answer = serializers.ChoiceField(choices=YesNo.choices, required=False)
    text = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)


class StepAnswersInputSerializer(StrictSerializer):
    answers = AnswerInputSerializer(many=True, allow_empty=True)


class CancelAssessmentSerializer(StrictSerializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class ReviewRequestInputSerializer(StrictSerializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")


class PaymentCreateSerializer(StrictSerializer):
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    provider_token = serializers.CharField(max_length=500)


class LabUploadCreateSerializer(StrictSerializer):
    assessment_id = serializers.UUIDField()
    file_name = serializers.CharField(min_length=1, max_length=150)
    content_type = serializers.ChoiceField(choices=["application/pdf", "image/png", "image/jpeg"])
    size_bytes = serializers.IntegerField(min_value=1, max_value=10 * 1024 * 1024)


class UploadTargetSerializer(serializers.Serializer[Any]):
    method = serializers.CharField()
    url = serializers.CharField()
    headers = serializers.DictField(child=serializers.CharField())
    expires_at = serializers.DateTimeField()


class LabUploadInitSerializer(serializers.ModelSerializer[LabUpload]):
    upload = serializers.SerializerMethodField()

    class Meta:
        model = LabUpload
        fields = ["id", "status", "upload"]
        read_only_fields = fields

    def get_upload(self, obj: LabUpload) -> dict[str, Any]:
        return cast(dict[str, Any], self.context["upload"])


class LabUploadSerializer(serializers.ModelSerializer[LabUpload]):
    rejection_reason = serializers.SerializerMethodField()

    class Meta:
        model = LabUpload
        fields = [
            "id",
            "assessment_id",
            "file_name",
            "size_bytes",
            "content_type",
            "status",
            "rejection_reason",
            "created_at",
        ]
        read_only_fields = fields

    def get_rejection_reason(self, obj: LabUpload) -> str | None:
        return obj.rejection_reason or None


class LabPanelSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()


class LabRecommendationsSerializer(serializers.Serializer[Any]):
    intro = serializers.CharField()
    group_label = serializers.CharField()
    recommended_by = serializers.ChoiceField(choices=["SYSTEM", "PHYSICIAN"])
    panels = LabPanelSerializer(many=True)


class SubmitAssessmentResponseSerializer(serializers.Serializer[Any]):
    assessment_id = serializers.UUIDField()
    evaluation_status = serializers.ChoiceField(choices=EvaluationStatus.choices)
    screening_result = serializers.ChoiceField(choices=ScreeningResult.choices)
    next = serializers.ChoiceField(choices=["SCREENED_OUT_MESSAGE", "PAY_FEE", "RECOMMENDED_LABS"])


class RequestReviewResponseSerializer(serializers.Serializer[Any]):
    evaluation_status = serializers.ChoiceField(choices=EvaluationStatus.choices)
    review_requested = serializers.BooleanField()


class LabsSubmitResponseSerializer(serializers.Serializer[Any]):
    labs_status = serializers.ChoiceField(choices=LabsStatus.choices)
    evaluation_status = serializers.ChoiceField(choices=EvaluationStatus.choices)


class LabsSkipResponseSerializer(serializers.Serializer[Any]):
    labs_status = serializers.ChoiceField(choices=LabsStatus.choices)


class DownloadUrlSerializer(serializers.Serializer[Any]):
    url = serializers.CharField()
    expires_at = serializers.DateTimeField()


def _evaluation(assessment: Assessment) -> Evaluation | None:
    try:
        return assessment.evaluation
    except Evaluation.DoesNotExist:
        return None


def _measurement(assessment: Assessment) -> AssessmentMeasurement | None:
    try:
        return assessment.measurement
    except AssessmentMeasurement.DoesNotExist:
        return None


__all__ = [
    "AlternativeCareSerializer",
    "AnswerInputSerializer",
    "AnswerOutputSerializer",
    "AssessmentCreateSerializer",
    "AssessmentDetailSerializer",
    "AssessmentSummarySerializer",
    "BmiScaleItemSerializer",
    "CancelAssessmentSerializer",
    "ChecklistItemSerializer",
    "DisplaySerializer",
    "DownloadUrlSerializer",
    "EvaluationSerializer",
    "FeeSerializer",
    "HeightOutputSerializer",
    "HeightQuantityInputSerializer",
    "JourneyItemSerializer",
    "LabPanelSerializer",
    "LabRecommendationsSerializer",
    "LabUploadCreateSerializer",
    "LabUploadInitSerializer",
    "LabUploadSerializer",
    "LabsSkipResponseSerializer",
    "LabsSubmitResponseSerializer",
    "MeasurementInputSerializer",
    "MeasurementsSerializer",
    "PaymentCreateSerializer",
    "PaymentSerializer",
    "PrescriptionSummarySerializer",
    "ProgramSerializer",
    "ProgramSummarySerializer",
    "ProjectionPointSerializer",
    "ProjectionSerializer",
    "QuantityInputSerializer",
    "QuestionOptionSerializer",
    "QuestionSerializer",
    "QuestionnaireSerializer",
    "RequestReviewResponseSerializer",
    "ReviewRequestInputSerializer",
    "ReviewingPhysicianSerializer",
    "StepAnswersInputSerializer",
    "StepAnswersSerializer",
    "StepSerializer",
    "SubmitAssessmentResponseSerializer",
    "SummarySerializer",
    "UploadTargetSerializer",
    "WeightOutputSerializer",
]
