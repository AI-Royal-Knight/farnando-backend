from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.consultations.models import CareRole
from apps.core.serializers import StrictSerializer
from apps.messaging.models import MessageSenderRole

PARTICIPANT_ROLES = [*CareRole.choices, ("PATIENT", "Patient")]


class ParticipantSerializer(serializers.Serializer[Any]):
    """Care-team member for patients; the patient for clinicians (Part 4, 8)."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=PARTICIPANT_ROLES)
    headline = serializers.CharField(required=False)
    age_years = serializers.IntegerField(required=False, allow_null=True)
    avatar_url = serializers.CharField(allow_null=True)


class LastMessageSerializer(serializers.Serializer[Any]):
    preview = serializers.CharField()
    sent_at = serializers.DateTimeField()
    sender_role = serializers.ChoiceField(choices=MessageSenderRole.choices)


class ThreadSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    participant = ParticipantSerializer()
    last_message = LastMessageSerializer(allow_null=True)
    unread_count = serializers.IntegerField()
    updated_at = serializers.DateTimeField()


class ActiveTreatmentRefSerializer(serializers.Serializer[Any]):
    treatment_id = serializers.UUIDField()
    medication_name = serializers.CharField()


class PatientContextSerializer(serializers.Serializer[Any]):
    patient_id = serializers.UUIDField()
    active_treatments = ActiveTreatmentRefSerializer(many=True)
    next_consultation = serializers.SerializerMethodField()

    def get_next_consultation(self, obj: Any) -> dict[str, Any] | None:
        from apps.consultations.serializers import ConsultationSummarySerializer

        consultation = obj["next_consultation"]
        return dict(ConsultationSummarySerializer(consultation).data) if consultation else None


class ThreadDetailSerializer(ThreadSerializer):
    safety_notice = serializers.CharField()
    patient_context = PatientContextSerializer(required=False)


class SenderSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=MessageSenderRole.choices)
    avatar_url = serializers.CharField(allow_null=True)


class MessageSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    thread_id = serializers.UUIDField()
    sender = SenderSerializer()
    body = serializers.CharField()
    sent_at = serializers.DateTimeField()
    read_at = serializers.DateTimeField(allow_null=True)
    is_mine = serializers.BooleanField()


class ThreadCreateSerializer(StrictSerializer):
    participant_id = serializers.UUIDField()
    body = serializers.CharField(max_length=4000, required=False, allow_blank=True)


class MessageCreateSerializer(StrictSerializer):
    body = serializers.CharField(max_length=20000, trim_whitespace=False)


class MarkReadSerializer(StrictSerializer):
    up_to_message_id = serializers.UUIDField()
