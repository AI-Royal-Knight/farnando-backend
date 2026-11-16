"""Part 3 section 4 request/response shapes."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.consultations import services
from apps.consultations.clinicians import physician_summary
from apps.consultations.models import Consultation, ConsultationStatus, ConsultationType
from apps.core.serializers import StrictSerializer


class PhysicianSummarySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    headline = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)


class ConsultationTypeSerializer(serializers.ModelSerializer[ConsultationType]):
    fee = serializers.SerializerMethodField()

    class Meta:
        model = ConsultationType
        fields = [
            "id",
            "code",
            "name",
            "description",
            "duration_minutes",
            "is_initial",
            "fee",
        ]
        read_only_fields = fields

    def get_fee(self, obj: ConsultationType) -> dict[str, str]:
        return {"amount": f"{obj.fee_amount:.2f}", "currency": obj.fee_currency}


class ConsultationTypeRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class ConsultationSummarySerializer(serializers.ModelSerializer[Consultation]):
    status = serializers.ChoiceField(choices=ConsultationStatus.choices)
    consultation_type = serializers.SerializerMethodField()
    physician = serializers.SerializerMethodField()
    can_join = serializers.SerializerMethodField()

    class Meta:
        model = Consultation
        fields = [
            "id",
            "status",
            "consultation_type",
            "physician",
            "start",
            "end",
            "timezone",
            "can_join",
        ]
        read_only_fields = fields

    def get_consultation_type(self, obj: Consultation) -> dict[str, Any]:
        return {"id": str(obj.consultation_type_id), "name": obj.consultation_type.name}

    def get_physician(self, obj: Consultation) -> dict[str, Any]:
        return physician_summary(obj.physician)

    def get_can_join(self, obj: Consultation) -> bool:
        return services.can_join(obj)


class ConsultationSerializer(ConsultationSummarySerializer):
    duration_minutes = serializers.SerializerMethodField()
    patient_note = serializers.SerializerMethodField()
    join_opens_at = serializers.SerializerMethodField()
    can_reschedule = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()

    class Meta(ConsultationSummarySerializer.Meta):
        fields = [
            "id",
            "status",
            "consultation_type",
            "physician",
            "start",
            "end",
            "timezone",
            "duration_minutes",
            "patient_note",
            "confirmed_at",
            "join_opens_at",
            "can_join",
            "can_reschedule",
            "can_cancel",
            "started_at",
            "ended_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_duration_minutes(self, obj: Consultation) -> int:
        return int((obj.end - obj.start).total_seconds() // 60)

    def get_patient_note(self, obj: Consultation) -> str | None:
        return obj.patient_note or None

    def get_join_opens_at(self, obj: Consultation) -> Any:
        return serializers.DateTimeField().to_representation(services.join_opens_at(obj))

    def get_can_reschedule(self, obj: Consultation) -> bool:
        return services.can_reschedule(obj)

    def get_can_cancel(self, obj: Consultation) -> bool:
        return services.can_cancel(obj)


class PhysicianListItemSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    headline = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)
    specialty = serializers.CharField()
    license_label = serializers.CharField(allow_null=True)
    next_available_at = serializers.DateTimeField(allow_null=True)


class SlotSerializer(serializers.Serializer[Any]):
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()


class AvailabilitySerializer(serializers.Serializer[Any]):
    physician_id = serializers.UUIDField()
    timezone = serializers.CharField()
    slot_minutes = serializers.IntegerField()
    slots = SlotSerializer(many=True)


class ConsultationCreateSerializer(StrictSerializer):
    physician_id = serializers.UUIDField()
    consultation_type_id = serializers.UUIDField()
    start = serializers.DateTimeField()
    assessment_id = serializers.UUIDField(required=False, allow_null=True)
    patient_note = serializers.CharField(
        max_length=500, required=False, allow_blank=True, allow_null=True
    )
    # The visit fee is charged in the same request when the type has one.
    payment_method = serializers.ChoiceField(choices=["CULQI"], required=False)
    provider_token = serializers.CharField(max_length=500, required=False, allow_blank=True)
    demo_payment = serializers.BooleanField(required=False, default=False)


class RescheduleSerializer(StrictSerializer):
    start = serializers.DateTimeField()


class ReasonSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class JoinResponseSerializer(serializers.Serializer[Any]):
    provider = serializers.CharField()
    room_url = serializers.CharField()
    token = serializers.CharField()
    display_name = serializers.CharField()
    expires_at = serializers.DateTimeField()
    consultation = ConsultationSerializer()
