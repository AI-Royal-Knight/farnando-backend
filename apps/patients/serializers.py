"""Part 3 section 2: profile, preferences, addresses, ubigeo and care team."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.accounts.serializers import UserSummarySerializer
from apps.consultations.models import CareRole
from apps.core.serializers import StrictSerializer
from apps.patients.models import Address, PatientProfile
from apps.patients.services import latest_measurement, validate_e164


class NotificationPreferencesSerializer(serializers.Serializer[Any]):
    email_messages = serializers.BooleanField()
    email_orders = serializers.BooleanField()
    email_reminders = serializers.BooleanField()
    email_marketing = serializers.BooleanField()


class PreferencesSerializer(serializers.Serializer[Any]):
    weight_unit = serializers.ChoiceField(choices=[("KG", "KG"), ("LB", "LB")])
    height_unit = serializers.ChoiceField(choices=[("CM", "CM"), ("IN", "IN")])
    language = serializers.ChoiceField(choices=[("es", "es"), ("en", "en")])
    timezone = serializers.CharField()
    notifications = NotificationPreferencesSerializer()


class PatientProfileSerializer(serializers.ModelSerializer[PatientProfile]):
    user = UserSummarySerializer(read_only=True)
    phone = serializers.SerializerMethodField()
    date_of_birth = serializers.SerializerMethodField()
    sex = serializers.SerializerMethodField()
    default_address_id = serializers.UUIDField(read_only=True, allow_null=True)
    preferences = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="user.date_joined", read_only=True)

    class Meta:
        model = PatientProfile
        fields = [
            "user",
            "phone",
            "date_of_birth",
            "sex",
            "default_address_id",
            "preferences",
            "created_at",
        ]
        read_only_fields = fields

    def get_phone(self, obj: PatientProfile) -> str | None:
        return obj.phone or None

    def get_date_of_birth(self, obj: PatientProfile) -> str | None:
        measurement = latest_measurement(obj.user)
        return measurement.date_of_birth.isoformat() if measurement else None

    def get_sex(self, obj: PatientProfile) -> str | None:
        measurement = latest_measurement(obj.user)
        return measurement.sex if measurement else None

    def get_preferences(self, obj: PatientProfile) -> dict[str, Any]:
        return {
            "weight_unit": obj.weight_unit,
            "height_unit": obj.height_unit,
            "language": obj.language,
            "timezone": obj.timezone,
            "notifications": {
                "email_messages": obj.email_messages,
                "email_orders": obj.email_orders,
                "email_reminders": obj.email_reminders,
                "email_marketing": obj.email_marketing,
            },
        }


# Documentation-only shape of PATCH /patients/me/ (validated in services).
class ProfileUserPatchSerializer(serializers.Serializer[Any]):
    full_name = serializers.CharField(min_length=2, max_length=120, required=False)


class NotificationPreferencesPatchSerializer(serializers.Serializer[Any]):
    email_messages = serializers.BooleanField(required=False)
    email_orders = serializers.BooleanField(required=False)
    email_reminders = serializers.BooleanField(required=False)
    email_marketing = serializers.BooleanField(required=False)


class PreferencesPatchSerializer(serializers.Serializer[Any]):
    weight_unit = serializers.ChoiceField(choices=[("KG", "KG"), ("LB", "LB")], required=False)
    height_unit = serializers.ChoiceField(choices=[("CM", "CM"), ("IN", "IN")], required=False)
    language = serializers.ChoiceField(choices=[("es", "es"), ("en", "en")], required=False)
    timezone = serializers.CharField(required=False)
    notifications = NotificationPreferencesPatchSerializer(required=False)


class PatientProfilePatchSerializer(serializers.Serializer[Any]):
    phone = serializers.CharField(required=False, allow_null=True)
    preferences = PreferencesPatchSerializer(required=False)
    user = ProfileUserPatchSerializer(required=False)


class AddressSerializer(serializers.ModelSerializer[Address]):
    ubigeo = serializers.CharField(source="ubigeo_id", read_only=True)
    department = serializers.SerializerMethodField()
    province = serializers.SerializerMethodField()
    district = serializers.SerializerMethodField()
    is_deliverable = serializers.SerializerMethodField()

    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "recipient_name",
            "phone",
            "line1",
            "line2",
            "reference",
            "ubigeo",
            "department",
            "province",
            "district",
            "postal_code",
            "country",
            "is_default",
            "is_deliverable",
        ]
        read_only_fields = fields

    def _chain(self, obj: Address) -> tuple[str, str, str]:
        district = obj.ubigeo
        province = district.parent
        department = province.parent if province is not None else None
        return (
            department.name if department is not None else "",
            province.name if province is not None else "",
            district.name,
        )

    def get_department(self, obj: Address) -> str:
        return self._chain(obj)[0]

    def get_province(self, obj: Address) -> str:
        return self._chain(obj)[1]

    def get_district(self, obj: Address) -> str:
        return self._chain(obj)[2]

    def get_is_deliverable(self, obj: Address) -> bool:
        return obj.is_deliverable


def address_snapshot(address: Address) -> dict[str, Any]:
    data = dict(AddressSerializer(address).data)
    data["id"] = str(address.pk)
    return data


class AddressInputSerializer(StrictSerializer):
    label = serializers.CharField(  # type: ignore[assignment]
        max_length=30, required=False, allow_blank=True
    )
    recipient_name = serializers.CharField(min_length=2, max_length=120)
    phone = serializers.CharField(max_length=16)
    line1 = serializers.CharField(max_length=200)
    line2 = serializers.CharField(max_length=100, required=False, allow_blank=True)
    reference = serializers.CharField(max_length=200, required=False, allow_blank=True)
    ubigeo = serializers.RegexField(r"^\d{6}$", max_length=6)
    postal_code = serializers.CharField(max_length=10, required=False, allow_blank=True)
    is_default = serializers.BooleanField(required=False)

    def validate_phone(self, value: str) -> str:
        return validate_e164(value, "phone")

    def validate_recipient_name(self, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("Must be 2 to 120 characters.")
        return value


class UbigeoSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    name = serializers.CharField()
    is_deliverable = serializers.BooleanField(allow_null=True)


class CareTeamMemberSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.ChoiceField(choices=CareRole.choices)
    headline = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)


__all__ = [
    "AddressInputSerializer",
    "AddressSerializer",
    "CareTeamMemberSerializer",
    "PatientProfilePatchSerializer",
    "PatientProfileSerializer",
    "PreferencesSerializer",
    "UbigeoSerializer",
    "address_snapshot",
]
