from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.assessments.enums import AssessmentStatus, BmiCategory, EvaluationStatus
from apps.assessments.serializers import (
    JourneyItemSerializer,
    ProjectionSerializer,
    WeightOutputSerializer,
)
from apps.consultations.serializers import ConsultationSummarySerializer
from apps.treatments.models import TimeOfDay

SECTION_KEYS = [
    ("OVERVIEW", "Overview"),
    ("ASSESSMENT", "Assessment"),
    ("CONSULTATIONS", "Consultations"),
    ("TREATMENT", "Treatment"),
    ("ORDERS", "Orders"),
    ("MESSAGES", "Messages"),
]

ACTION_TYPES = [
    ("RESUME_ASSESSMENT", "Resume assessment"),
    ("PAY_FEE", "Pay fee"),
    ("UPLOAD_LABS", "Upload labs"),
    ("SET_APPOINTMENT", "Set appointment"),
    ("ORDER_MEDICINE", "Order medicine"),
    ("VIEW_TREATMENT", "View treatment"),
    ("NONE", "None"),
]


class SectionSerializer(serializers.Serializer[Any]):
    key = serializers.ChoiceField(choices=SECTION_KEYS)
    enabled = serializers.BooleanField()


class OverviewAssessmentSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    program_name = serializers.CharField()
    status = serializers.ChoiceField(choices=AssessmentStatus.choices)
    evaluation_status = serializers.ChoiceField(choices=EvaluationStatus.choices, allow_null=True)
    journey = JourneyItemSerializer(many=True)
    current_step_key = serializers.CharField(allow_null=True)


class HealthSummarySerializer(serializers.Serializer[Any]):
    headline = serializers.CharField()
    subheadline = serializers.CharField()
    bmi = serializers.FloatField()
    bmi_category = serializers.ChoiceField(choices=BmiCategory.choices)
    current_weight = WeightOutputSerializer()
    target_weight = WeightOutputSerializer()
    projection = ProjectionSerializer()
    disclaimer = serializers.CharField()


class PrimaryActionSerializer(serializers.Serializer[Any]):
    type = serializers.ChoiceField(choices=ACTION_TYPES)
    label = serializers.CharField(allow_blank=True)  # type: ignore[assignment]
    target_id = serializers.UUIDField(allow_null=True)


class NextDoseOverviewSerializer(serializers.Serializer[Any]):
    treatment_id = serializers.UUIDField()
    medication_name = serializers.CharField()
    due_at = serializers.DateTimeField(allow_null=True)
    time_of_day = serializers.ChoiceField(choices=TimeOfDay.choices)


class OverviewSerializer(serializers.Serializer[Any]):
    display_name = serializers.CharField(allow_blank=True)
    sections = SectionSerializer(many=True)
    assessment = OverviewAssessmentSerializer(allow_null=True)
    health_summary = HealthSummarySerializer(allow_null=True)
    primary_action = PrimaryActionSerializer()
    next_consultation = ConsultationSummarySerializer(allow_null=True)
    next_dose = NextDoseOverviewSerializer(allow_null=True)
    active_orders_count = serializers.IntegerField()
    unread_messages_count = serializers.IntegerField()
    unread_notifications_count = serializers.IntegerField()
