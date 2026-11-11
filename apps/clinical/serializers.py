"""Part 4 sections 3-7 request and response shapes."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.assessments.enums import (
    BmiCategory,
    EvaluationStatus,
    RejectionReasonCode,
    ReviewGroup,
    ScreeningResult,
    Sex,
)
from apps.clinical.models import NoteKind
from apps.consultations.models import (
    AvailabilityKind,
    ConsultationStatus,
    TimeBlockKind,
)
from apps.consultations.serializers import PhysicianSummarySerializer
from apps.core.serializers import StrictSerializer
from apps.treatments.models import (
    AdministrationRoute,
    DoseFrequency,
    RenewalRequestStatus,
    TimeOfDay,
    TreatmentStatus,
)

QUEUE_LABS_STATES = [("ATTACHED", "Attached"), ("SCANNING", "Scanning"), ("MISSING", "Missing")]
PATIENT_LIST_STATUSES = [
    ("NEWLY_APPROVED", "Newly approved"),
    ("ACTIVE_TREATMENT", "Active treatment"),
    ("PAUSED", "Paused"),
    ("COMPLETED", "Completed"),
    ("DISCONTINUED", "Discontinued"),
]


class RefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class PatientBriefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    age_years = serializers.IntegerField(allow_null=True)
    sex = serializers.ChoiceField(choices=Sex.choices, allow_null=True)


class ClinicalEvaluationItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    version = serializers.IntegerField()
    status = serializers.ChoiceField(choices=EvaluationStatus.choices)
    patient = PatientBriefSerializer()
    program = RefSerializer()
    submitted_at = serializers.DateTimeField()
    questionnaire_state = serializers.CharField()
    labs_state = serializers.ChoiceField(choices=QUEUE_LABS_STATES)
    bmi = serializers.FloatField(allow_null=True)
    bmi_category = serializers.ChoiceField(choices=BmiCategory.choices, allow_null=True)
    screening_result = serializers.ChoiceField(choices=ScreeningResult.choices, allow_null=True)
    assigned_physician = PhysicianSummarySerializer(allow_null=True)


class QueueCountsSerializer(serializers.Serializer[Any]):
    all = serializers.IntegerField()
    queue = serializers.IntegerField()
    mine = serializers.IntegerField()


class QueueResponseSerializer(serializers.Serializer[Any]):
    counts = QueueCountsSerializer()
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ClinicalEvaluationItemSerializer(many=True)


class ReviewItemSerializer(serializers.Serializer[Any]):
    question_text = serializers.CharField()
    answer = serializers.CharField()


class ReviewGroupBlockSerializer(serializers.Serializer[Any]):
    group = serializers.ChoiceField(choices=ReviewGroup.choices)
    title = serializers.CharField()
    items = ReviewItemSerializer(many=True)


class ScreeningFlagSerializer(serializers.Serializer[Any]):
    question_id = serializers.UUIDField()
    question_text = serializers.CharField()
    option_id = serializers.UUIDField(allow_null=True)
    option_label = serializers.CharField()
    effect = serializers.ChoiceField(
        choices=[("NEEDS_REVIEW", "Needs review"), ("NOT_ELIGIBLE", "Not eligible")]
    )


class LabAttachmentSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    file_name = serializers.CharField()
    size_bytes = serializers.IntegerField()
    content_type = serializers.CharField()
    uploaded_by = serializers.CharField()
    uploaded_at = serializers.DateTimeField()
    status = serializers.CharField()


class QuantitySerializer(serializers.Serializer[Any]):
    kg = serializers.FloatField(required=False)
    cm = serializers.FloatField(required=False)
    display = serializers.DictField()


class BiometricsSerializer(serializers.Serializer[Any]):
    current_weight = QuantitySerializer()
    target_weight = QuantitySerializer()
    weight_goal_kg = serializers.FloatField()
    height = QuantitySerializer()
    bmi = serializers.FloatField()
    bmi_category = serializers.ChoiceField(choices=BmiCategory.choices)


class PacketPatientSerializer(PatientBriefSerializer):
    date_of_birth = serializers.DateField(allow_null=True)


class CodeNameSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    name = serializers.CharField()


class PreviousEvaluationSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    program_name = serializers.CharField()
    status = serializers.ChoiceField(choices=EvaluationStatus.choices)
    decided_at = serializers.DateTimeField(allow_null=True)


class ExistingTreatmentSerializer(serializers.Serializer[Any]):
    treatment_id = serializers.UUIDField()
    medication_name = serializers.CharField()
    status = serializers.ChoiceField(choices=TreatmentStatus.choices)


class ReviewPacketSerializer(ClinicalEvaluationItemSerializer):
    patient = PacketPatientSerializer()
    biometrics = BiometricsSerializer(allow_null=True)
    questionnaire = ReviewGroupBlockSerializer(many=True)
    screening_flags = ScreeningFlagSerializer(many=True)
    attachments = LabAttachmentSerializer(many=True)
    lab_recommendations = CodeNameSerializer(many=True)
    previous_evaluations = PreviousEvaluationSerializer(many=True)
    existing_treatments = ExistingTreatmentSerializer(many=True)
    patient_note = serializers.CharField(allow_null=True)
    internal_note = serializers.CharField(allow_null=True)


class LabPanelCatalogSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()


class ReleaseReasonSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RequestLabsSerializer(StrictSerializer):
    panels = serializers.ListField(
        child=serializers.CharField(max_length=40), min_length=1, max_length=10
    )
    message = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RejectSerializer(StrictSerializer):
    reason_code = serializers.ChoiceField(choices=RejectionReasonCode.choices)
    internal_note = serializers.CharField(min_length=10, max_length=2000)
    patient_note = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    attestation = serializers.BooleanField()

    def validate_attestation(self, value: bool) -> bool:
        if value is not True:
            raise serializers.ValidationError("You must confirm the attestation.")
        return value


class DoseSerializer(StrictSerializer):
    value = serializers.DecimalField(max_digits=8, decimal_places=3, min_value=0)
    unit = serializers.CharField(max_length=16)


class DoseOutSerializer(serializers.Serializer[Any]):
    value = serializers.FloatField()
    unit = serializers.CharField()
    label = serializers.CharField()  # type: ignore[assignment]


class ProductSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    active_ingredient = serializers.CharField()
    form = serializers.CharField()
    route = serializers.ChoiceField(choices=AdministrationRoute.choices, allow_blank=True)
    allowed_doses = DoseOutSerializer(many=True)
    default_frequency = serializers.ChoiceField(choices=DoseFrequency.choices)
    starting_doses = DoseOutSerializer(many=True)
    max_refills = serializers.IntegerField()


class PrescriptionItemSerializer(StrictSerializer):
    product_id = serializers.UUIDField()
    dose = DoseSerializer()
    route = serializers.ChoiceField(choices=AdministrationRoute.choices)
    frequency = serializers.ChoiceField(choices=DoseFrequency.choices)
    day_of_week = serializers.IntegerField(
        min_value=1, max_value=7, required=False, allow_null=True
    )
    time_of_day = serializers.ChoiceField(choices=TimeOfDay.choices)
    duration_weeks = serializers.IntegerField(min_value=1, max_value=52)
    refills_total = serializers.IntegerField(min_value=0)
    instructions = serializers.CharField(min_length=1, max_length=1000)


class PrescriptionInputSerializer(StrictSerializer):
    start_date = serializers.DateField()
    valid_until = serializers.DateField()
    items = serializers.ListField(child=PrescriptionItemSerializer(), min_length=1, max_length=5)


class ApproveSerializer(StrictSerializer):
    attestation = serializers.BooleanField()
    acknowledged_flag_option_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    prescription = PrescriptionInputSerializer()
    signed_document_id = serializers.UUIDField(required=False, allow_null=True)
    patient_note = serializers.CharField(
        max_length=1000, required=False, allow_null=True, allow_blank=True
    )
    internal_note = serializers.CharField(
        max_length=2000, required=False, allow_null=True, allow_blank=True
    )

    def validate_attestation(self, value: bool) -> bool:
        if value is not True:
            raise serializers.ValidationError("You must confirm the attestation.")
        return value


class PrescriptionOutItemSerializer(serializers.Serializer[Any]):
    treatment_id = serializers.UUIDField()
    product_id = serializers.UUIDField()
    dose = DoseOutSerializer()
    frequency = serializers.ChoiceField(choices=DoseFrequency.choices)
    refills_total = serializers.IntegerField()


class PrescriptionOutSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    start_date = serializers.DateField()
    valid_until = serializers.DateField()
    items = PrescriptionOutItemSerializer(many=True)


class ApproveResponseSerializer(serializers.Serializer[Any]):
    evaluation = ClinicalEvaluationItemSerializer()
    prescription = PrescriptionOutSerializer()


class PrescriptionDocumentCreateSerializer(StrictSerializer):
    evaluation_id = serializers.UUIDField()
    content_type = serializers.ChoiceField(choices=[("application/pdf", "PDF")])
    size_bytes = serializers.IntegerField(min_value=1)


class PrescriptionDocumentSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.CharField()
    content_type = serializers.CharField()
    size_bytes = serializers.IntegerField()
    created_at = serializers.DateTimeField()


# --- Section 5 ----------------------------------------------------------------


class PatientListItemSerializer(serializers.Serializer[Any]):
    patient = PatientBriefSerializer()
    program = RefSerializer(allow_null=True)
    medication_label = serializers.CharField(allow_blank=True)
    status = serializers.ChoiceField(choices=PATIENT_LIST_STATUSES, allow_null=True)
    last_activity_at = serializers.DateTimeField(allow_null=True)


class ScheduleOutSerializer(serializers.Serializer[Any]):
    frequency = serializers.ChoiceField(choices=DoseFrequency.choices)
    day_of_week = serializers.IntegerField(allow_null=True)
    time_of_day = serializers.ChoiceField(choices=TimeOfDay.choices)


class RefillsOutSerializer(serializers.Serializer[Any]):
    remaining = serializers.IntegerField()
    total = serializers.IntegerField()


class TreatmentVersionOutSerializer(serializers.Serializer[Any]):
    version = serializers.IntegerField()
    dose = DoseOutSerializer()
    effective_from = serializers.DateField()
    reason = serializers.CharField()
    changed_by = PhysicianSummarySerializer()
    changed_at = serializers.DateTimeField()


class ClinicianTreatmentSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=TreatmentStatus.choices)
    patient = PatientBriefSerializer()
    program = RefSerializer()
    product = RefSerializer(allow_null=True)
    current_dose = DoseOutSerializer()
    schedule = ScheduleOutSerializer()
    refills = RefillsOutSerializer()
    next_dose_due_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateField()
    valid_until = serializers.DateField(allow_null=True)
    versions = TreatmentVersionOutSerializer(many=True, required=False)


class ChartPatientSerializer(PatientBriefSerializer):
    date_of_birth = serializers.DateField(allow_null=True)
    email = serializers.EmailField(allow_null=True)
    phone = serializers.CharField(allow_null=True)


class NoteSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=NoteKind.choices)
    body = serializers.CharField()
    author = PhysicianSummarySerializer()
    created_at = serializers.DateTimeField()
    consultation_id = serializers.UUIDField(allow_null=True)


class NoteCreateSerializer(StrictSerializer):
    kind = serializers.ChoiceField(choices=NoteKind.choices)
    body = serializers.CharField(min_length=1, max_length=5000)
    consultation_id = serializers.UUIDField(required=False, allow_null=True)


class AdjustSerializer(StrictSerializer):
    dose = DoseSerializer()
    frequency = serializers.ChoiceField(choices=DoseFrequency.choices, required=False)
    day_of_week = serializers.IntegerField(
        min_value=1, max_value=7, required=False, allow_null=True
    )
    time_of_day = serializers.ChoiceField(choices=TimeOfDay.choices, required=False)
    instructions = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    effective_from = serializers.DateField(required=False)
    reason = serializers.CharField(min_length=10, max_length=1000)
    patient_note = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class StatusChangeSerializer(StrictSerializer):
    reason = serializers.CharField(min_length=10, max_length=1000)
    patient_note = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class RenewalItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=RenewalRequestStatus.choices)
    patient = PatientBriefSerializer()
    treatment = ClinicianTreatmentSerializer()
    patient_note = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class RenewalApproveSerializer(StrictSerializer):
    refills_added = serializers.IntegerField(min_value=1)
    valid_until = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RenewalDenySerializer(StrictSerializer):
    reason = serializers.CharField(min_length=10, max_length=1000)
    patient_note = serializers.CharField(max_length=1000, required=False, allow_blank=True)


# --- Section 6 ----------------------------------------------------------------


class ClinicianConsultationItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=ConsultationStatus.choices)
    patient = PatientBriefSerializer()
    mode = serializers.ChoiceField(choices=[("TELEHEALTH", "Telehealth")])
    consultation_type = RefSerializer()
    reason = serializers.CharField(allow_null=True)
    agenda = serializers.CharField(allow_null=True)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    timezone = serializers.CharField()
    can_join = serializers.BooleanField()
    can_accept = serializers.BooleanField()


class FollowUpSerializer(StrictSerializer):
    consultation_type_id = serializers.UUIDField()
    suggested_in_days = serializers.IntegerField(min_value=1, max_value=180)


class FollowUpOutSerializer(serializers.Serializer[Any]):
    consultation_type_id = serializers.UUIDField()
    suggested_in_days = serializers.IntegerField()


class ClinicianConsultationDetailSerializer(ClinicianConsultationItemSerializer):
    patient_note = serializers.CharField(allow_null=True)
    join_opens_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(allow_null=True)
    ended_at = serializers.DateTimeField(allow_null=True)
    clinical_notes = serializers.CharField(allow_null=True)
    visit_summary = serializers.CharField(allow_null=True)
    follow_up = FollowUpOutSerializer(allow_null=True)
    evaluation_id = serializers.UUIDField(allow_null=True)


class ConsultationMetaSerializer(serializers.Serializer[Any]):
    this_week_count = serializers.IntegerField()
    requested_count = serializers.IntegerField()


class ConsultationListResponseSerializer(serializers.Serializer[Any]):
    meta = ConsultationMetaSerializer()
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = ClinicianConsultationItemSerializer(many=True)


class DeclineSerializer(StrictSerializer):
    reason = serializers.CharField(min_length=1, max_length=500)
    patient_note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RequiredReasonSerializer(StrictSerializer):
    reason = serializers.CharField(min_length=1, max_length=500)


class PhysicianRescheduleSerializer(StrictSerializer):
    start = serializers.DateTimeField()
    reason = serializers.CharField(min_length=1, max_length=500)


class AgendaSerializer(StrictSerializer):
    agenda = serializers.CharField(max_length=300, required=False, allow_blank=True)


class CompleteSerializer(StrictSerializer):
    clinical_notes = serializers.CharField(min_length=1, max_length=10000)
    visit_summary = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    follow_up = FollowUpSerializer(required=False, allow_null=True)


class NoShowSerializer(StrictSerializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


# --- Section 3 and 7 ------------------------------------------------------------


class DashboardSerializer(serializers.Serializer[Any]):
    awaiting_evaluation_count = serializers.IntegerField()
    in_treatment_count = serializers.IntegerField()
    consultations_this_week_count = serializers.IntegerField()
    pending_renewal_requests_count = serializers.IntegerField()
    unread_messages_count = serializers.IntegerField()
    unread_notifications_count = serializers.IntegerField()
    today_consultations = ClinicianConsultationItemSerializer(many=True)


class AvailabilityRuleSerializer(StrictSerializer):
    id = serializers.UUIDField(read_only=True)
    kind = serializers.ChoiceField(choices=AvailabilityKind.choices)
    day_of_week = serializers.IntegerField(min_value=1, max_value=7)
    start_time = serializers.TimeField(format="%H:%M", input_formats=["%H:%M"])
    end_time = serializers.TimeField(format="%H:%M", input_formats=["%H:%M"])
    consultation_type_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True
    )
    valid_from = serializers.DateField(required=False, allow_null=True)
    valid_until = serializers.DateField(required=False, allow_null=True)
    label = serializers.CharField(  # type: ignore[assignment]
        max_length=60, required=False, allow_null=True, allow_blank=True
    )


class TimeBlockSerializer(StrictSerializer):
    id = serializers.UUIDField(read_only=True)
    kind = serializers.ChoiceField(choices=TimeBlockKind.choices)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    label = serializers.CharField(  # type: ignore[assignment]
        max_length=60, required=False, allow_null=True, allow_blank=True
    )


class CalendarBlockSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    label = serializers.CharField(allow_null=True)  # type: ignore[assignment]
    source = serializers.ChoiceField(  # type: ignore[assignment]
        choices=[("RULE", "Rule"), ("TIME_BLOCK", "Time block")]
    )


class CalendarItemSerializer(serializers.Serializer[Any]):
    type = serializers.ChoiceField(
        choices=[
            ("AVAILABLE_SLOT", "Available slot"),
            ("CONSULTATION", "Consultation"),
            ("BLOCK", "Block"),
        ]
    )
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    consultation = ClinicianConsultationItemSerializer(allow_null=True)
    block = CalendarBlockSerializer(allow_null=True)


class CalendarSerializer(serializers.Serializer[Any]):
    timezone = serializers.CharField()
    items = CalendarItemSerializer(many=True)
