"""Contact and complaints-book contracts."""

from __future__ import annotations

import re
from typing import Any

from django.utils.html import strip_tags
from rest_framework import serializers

from apps.catalog.models import ComplaintCategory
from apps.core.serializers import StrictSerializer
from apps.support.models import ComplaintBookEntry


class ContactMessageSerializer(StrictSerializer):
    full_name = serializers.CharField(max_length=120, min_length=2)
    email = serializers.EmailField(max_length=254)
    message = serializers.CharField(min_length=10, max_length=2000)
    captcha_token = serializers.CharField(write_only=True, required=False, allow_blank=True)

    def validate_message(self, value: str) -> str:
        cleaned = strip_tags(value).strip()
        if len(cleaned) < 10:
            raise serializers.ValidationError("Enter at least 10 characters.")
        return cleaned

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class ContactMessageResponseSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


_PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_DOCUMENT_RE = {
    "DNI": re.compile(r"^\d{8}$"),
    "CE": re.compile(r"^[A-Za-z0-9]{9,12}$"),
    "PASSPORT": re.compile(r"^[A-Za-z0-9]{6,20}$"),
    "RUC": re.compile(r"^\d{11}$"),
}


class ComplaintCreateSerializer(StrictSerializer):
    full_name = serializers.CharField(max_length=120, min_length=2)
    email = serializers.EmailField(max_length=254)
    category_code = serializers.CharField(max_length=32)
    filing_type = serializers.ChoiceField(choices=ComplaintBookEntry.FilingType.choices)
    description = serializers.CharField(min_length=20, max_length=4000)
    order_reference = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )
    document_type = serializers.ChoiceField(
        choices=ComplaintBookEntry.DocumentType.choices,
        required=False,
        allow_blank=True,
        default="",
    )
    document_number = serializers.CharField(
        max_length=32, required=False, allow_blank=True, default=""
    )
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    captcha_token = serializers.CharField(write_only=True, required=False, allow_blank=True)

    def validate_email(self, value: str) -> str:
        return value.strip().lower()

    def validate_category_code(self, value: str) -> str:
        if not ComplaintCategory.objects.filter(code=value, is_active=True).exists():
            raise serializers.ValidationError("Unknown complaint category.")
        return value

    def validate_description(self, value: str) -> str:
        cleaned = strip_tags(value).strip()
        if len(cleaned) < 20:
            raise serializers.ValidationError("Enter at least 20 characters.")
        return cleaned

    def validate_phone(self, value: str) -> str:
        if value and not _PHONE_RE.match(value):
            raise serializers.ValidationError("Enter a valid E.164 phone number.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        document_type = attrs.get("document_type", "")
        document_number = attrs.get("document_number", "")
        if bool(document_type) != bool(document_number):
            raise serializers.ValidationError(
                {"document_number": ["Document type and number must be provided together."]}
            )
        if document_type and document_number:
            pattern = _DOCUMENT_RE[document_type]
            if not pattern.match(document_number):
                raise serializers.ValidationError(
                    {"document_number": ["Invalid document number for the selected type."]}
                )
        return attrs


class ComplaintCreatedSerializer(serializers.ModelSerializer[ComplaintBookEntry]):
    class Meta:
        model = ComplaintBookEntry
        fields = ["complaint_number", "status", "created_at", "response_due_at"]
        read_only_fields = fields


class ComplaintStatusSerializer(serializers.ModelSerializer[ComplaintBookEntry]):
    class Meta:
        model = ComplaintBookEntry
        fields = [
            "complaint_number",
            "status",
            "created_at",
            "response_due_at",
            "responded_at",
        ]
        read_only_fields = fields
