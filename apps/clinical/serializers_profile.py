"""Part 4 section 2 shapes."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.accounts.serializers import ConsentInputSerializer, UserSummarySerializer
from apps.clinical.models import InvitationStatus
from apps.clinical.onboarding import avatar_url_for
from apps.consultations.models import ClinicianProfile, PhysicianVerificationStatus
from apps.core.serializers import StrictSerializer


class InvitationSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField()
    status = serializers.ChoiceField(choices=InvitationStatus.choices)
    expires_at = serializers.DateTimeField()
    invited_by_name = serializers.CharField(allow_null=True)


class SpecialtySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    requires_registration_number = serializers.BooleanField()


class InvitationAcceptSerializer(StrictSerializer):
    full_name = serializers.CharField(min_length=2, max_length=120)
    license_number = serializers.RegexField(r"^[A-Za-z0-9]{3,20}$")
    specialty_id = serializers.UUIDField()
    specialty_registration_number = serializers.RegexField(
        r"^[A-Za-z0-9]{3,20}$", required=False, allow_blank=True
    )
    phone = serializers.CharField(max_length=16)
    password = serializers.CharField(min_length=8, write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)
    avatar_upload_id = serializers.UUIDField(required=False, allow_null=True)
    consents = ConsentInputSerializer(many=True)

    def validate_phone(self, value: str) -> str:
        from apps.patients.services import validate_e164

        return validate_e164(value, "phone")


class PhysicianRegisterSerializer(StrictSerializer):
    full_name = serializers.CharField(min_length=2, max_length=120)
    email = serializers.EmailField(max_length=254)
    license_number = serializers.RegexField(r"^[A-Za-z0-9-]{3,20}$")
    specialty_id = serializers.UUIDField()
    specialty_registration_number = serializers.RegexField(
        r"^[A-Za-z0-9]{3,20}$", required=False, allow_blank=True
    )
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    password = serializers.CharField(min_length=8, write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)
    consents = ConsentInputSerializer(many=True)

    def validate_phone(self, value: str) -> str:
        if not value:
            return ""
        from apps.patients.services import validate_e164

        return validate_e164(value, "phone")


class PhysicianRegisterResponseSerializer(serializers.Serializer[Any]):
    status = serializers.CharField()


class AcceptResponseSerializer(serializers.Serializer[Any]):
    user = UserSummarySerializer()
    tokens = serializers.DictField(child=serializers.CharField())
    mfa_enrollment_required = serializers.BooleanField()


class AvatarUploadCreateSerializer(StrictSerializer):
    content_type = serializers.ChoiceField(
        choices=[("image/png", "PNG"), ("image/jpeg", "JPEG"), ("image/webp", "WebP")]
    )
    size_bytes = serializers.IntegerField(min_value=1)
    invitation_token = serializers.CharField(max_length=200, required=False)


class AvatarConfirmInputSerializer(StrictSerializer):
    invitation_token = serializers.CharField(max_length=200, required=False)


class UploadTargetSerializer(serializers.Serializer[Any]):
    method = serializers.CharField()
    url = serializers.CharField()
    headers = serializers.DictField(child=serializers.CharField())
    expires_at = serializers.DateTimeField()


class UploadInitSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    upload = UploadTargetSerializer()


class AvatarReadySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.CharField()
    preview_url = serializers.CharField()


class SpecialtyRefSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()


class PhysicianProfileSerializer(serializers.ModelSerializer[ClinicianProfile]):
    user = UserSummarySerializer(read_only=True)
    full_name = serializers.CharField(source="display_name", read_only=True)
    avatar_url = serializers.SerializerMethodField()
    bio = serializers.SerializerMethodField()
    specialty = serializers.SerializerMethodField()
    license_number = serializers.SerializerMethodField()
    specialty_registration_number = serializers.SerializerMethodField()
    verification_status = serializers.ChoiceField(choices=PhysicianVerificationStatus.choices)
    verification_note = serializers.SerializerMethodField()
    accepting_new_consultations = serializers.BooleanField(source="accepts_new_consultations")
    languages = serializers.ListField(child=serializers.CharField())

    class Meta:
        model = ClinicianProfile
        fields = [
            "id",
            "user",
            "full_name",
            "avatar_url",
            "headline",
            "bio",
            "specialty",
            "license_number",
            "specialty_registration_number",
            "phone",
            "verification_status",
            "verification_note",
            "accepting_new_consultations",
            "timezone",
            "languages",
            "created_at",
        ]
        read_only_fields = fields

    def get_avatar_url(self, obj: ClinicianProfile) -> str | None:
        return avatar_url_for(obj)

    def get_bio(self, obj: ClinicianProfile) -> str | None:
        return obj.bio or None

    def get_specialty(self, obj: ClinicianProfile) -> dict[str, str] | None:
        if obj.specialty_ref is None:
            return None
        return {"id": str(obj.specialty_ref_id), "name": obj.specialty_ref.name}

    def get_license_number(self, obj: ClinicianProfile) -> str:
        return obj.license_number or ""

    def get_specialty_registration_number(self, obj: ClinicianProfile) -> str | None:
        return obj.specialty_registration_number or None

    def get_verification_note(self, obj: ClinicianProfile) -> str | None:
        if obj.verification_status in (
            PhysicianVerificationStatus.REJECTED,
            PhysicianVerificationStatus.SUSPENDED,
        ):
            return obj.verification_note or None
        return None


class PhysicianProfilePatchSerializer(serializers.Serializer[Any]):
    """Documentation only; validation lives in ``onboarding.update_profile``."""

    avatar_upload_id = serializers.UUIDField(required=False)
    headline = serializers.CharField(max_length=120, required=False)
    bio = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    phone = serializers.CharField(required=False)
    accepting_new_consultations = serializers.BooleanField(required=False)
    timezone = serializers.CharField(required=False)
    languages = serializers.ListField(child=serializers.CharField(), required=False)
