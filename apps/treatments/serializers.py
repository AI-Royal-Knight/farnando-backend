"""Part 3 section 5 shapes."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.consultations.clinicians import physician_summary
from apps.consultations.serializers import PhysicianSummarySerializer
from apps.core.serializers import StrictSerializer
from apps.treatments import services
from apps.treatments.models import (
    AdministrationRoute,
    DoseFrequency,
    TimeOfDay,
    Treatment,
    TreatmentStatus,
)


class NextDoseSerializer(serializers.Serializer[Any]):
    due_at = serializers.DateTimeField(allow_null=True)
    time_of_day = serializers.ChoiceField(choices=TimeOfDay.choices)


class RefillsSerializer(serializers.Serializer[Any]):
    remaining = serializers.IntegerField()
    total = serializers.IntegerField()


class TreatmentListItemSerializer(serializers.ModelSerializer[Treatment]):
    status = serializers.ChoiceField(choices=TreatmentStatus.choices)
    program_name = serializers.CharField(source="program.name", read_only=True)
    image_url = serializers.SerializerMethodField()
    next_dose = serializers.SerializerMethodField()
    refills = serializers.SerializerMethodField()
    prescribing_physician = serializers.SerializerMethodField()

    class Meta:
        model = Treatment
        fields = [
            "id",
            "status",
            "program_name",
            "medication_name",
            "strength_label",
            "image_url",
            "next_dose",
            "schedule_label",
            "refills",
            "prescribing_physician",
        ]
        read_only_fields = fields

    def get_image_url(self, obj: Treatment) -> str | None:
        return (obj.product.image_url or None) if obj.product else None

    def _tz(self) -> str | None:
        value = self.context.get("tz_name")
        return str(value) if value else None

    def get_next_dose(self, obj: Treatment) -> dict[str, Any]:
        due = services.next_dose_due(obj, tz_name=self._tz())
        return {
            "due_at": serializers.DateTimeField().to_representation(due) if due else None,
            "time_of_day": obj.time_of_day,
        }

    def get_refills(self, obj: Treatment) -> dict[str, int] | None:
        if not obj.has_refills:
            return None
        return {"remaining": int(obj.refills_remaining or 0), "total": int(obj.refills_total or 0)}

    def get_prescribing_physician(self, obj: Treatment) -> dict[str, Any]:
        return physician_summary(obj.prescribing_physician)


class TreatmentDetailSerializer(TreatmentListItemSerializer):
    description = serializers.SerializerMethodField()
    route = serializers.ChoiceField(choices=AdministrationRoute.choices)
    schedule = serializers.SerializerMethodField()
    current_dose = serializers.SerializerMethodField()
    lifestyle_essentials = serializers.SerializerMethodField()
    next_check_in = serializers.SerializerMethodField()
    safety_information_markdown = serializers.SerializerMethodField()
    prescription_pdf_available = serializers.SerializerMethodField()
    last_order_id = serializers.SerializerMethodField()

    class Meta(TreatmentListItemSerializer.Meta):
        fields = [
            *TreatmentListItemSerializer.Meta.fields,
            "title",
            "description",
            "route",
            "schedule",
            "current_dose",
            "started_at",
            "lifestyle_essentials",
            "next_check_in",
            "safety_information_markdown",
            "prescription_pdf_available",
            "last_order_id",
        ]
        read_only_fields = fields

    def get_description(self, obj: Treatment) -> str:
        return obj.product.description if obj.product else ""

    def get_schedule(self, obj: Treatment) -> dict[str, Any]:
        return {
            "frequency": obj.frequency,
            "day_of_week": obj.day_of_week,
            "time_of_day": obj.time_of_day,
        }

    def get_current_dose(self, obj: Treatment) -> dict[str, Any]:
        value = float(obj.dose_value)
        label = obj.dose_label or f"{value:g} {obj.dose_unit}"
        return {"value": value, "unit": obj.dose_unit, "label": label}

    def get_lifestyle_essentials(self, obj: Treatment) -> list[dict[str, str]]:
        items = obj.program.lifestyle_essentials
        if not isinstance(items, list):
            return []
        return [
            {"title": str(item.get("title", "")), "body": str(item.get("body", ""))}
            for item in items
            if isinstance(item, dict)
        ]

    def get_next_check_in(self, obj: Treatment) -> dict[str, Any] | None:
        if obj.next_check_in_date is None:
            return None
        return {
            "date": obj.next_check_in_date.isoformat(),
            "consultation_id": (
                str(obj.next_check_in_consultation_id)
                if obj.next_check_in_consultation_id
                else None
            ),
        }

    def get_safety_information_markdown(self, obj: Treatment) -> str | None:
        if obj.product is None:
            return None
        return obj.product.safety_information_markdown or None

    def get_prescription_pdf_available(self, obj: Treatment) -> bool:
        return services.prescription_pdf_available(obj)

    def get_last_order_id(self, obj: Treatment) -> str | None:
        order = obj.orders.order_by("-placed_at").only("id").first()
        return str(order.pk) if order else None


class ScheduleSerializer(serializers.Serializer[Any]):
    frequency = serializers.ChoiceField(choices=DoseFrequency.choices)
    day_of_week = serializers.IntegerField(allow_null=True)
    time_of_day = serializers.ChoiceField(choices=TimeOfDay.choices)


class RenewalRequestInputSerializer(StrictSerializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RenewalRequestSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


__all__ = [
    "PhysicianSummarySerializer",
    "RenewalRequestInputSerializer",
    "RenewalRequestSerializer",
    "TreatmentDetailSerializer",
    "TreatmentListItemSerializer",
]
