"""Request/response contracts for authentication, legal documents and consents."""

from __future__ import annotations

import re
from typing import Any

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenRefreshSerializer

from apps.accounts.models import ConsentRecord, LegalDocument, User
from apps.accounts.services import AccountDisabled, ConsentService, issue_tokens
from apps.accounts.tokens import (
    EXPIRED,
    parse_password_reset_token,
)
from apps.core.exceptions import (
    AccountLocked,
    ConsentRequiredInput,
    InvalidCredentials,
    TokenExpiredInput,
    TokenInvalidInput,
)
from apps.core.serializers import StrictFieldsMixin, StrictSerializer

_NAME_RE = re.compile(r"[^\W\d_]", re.UNICODE)


class UserSummarySerializer(serializers.ModelSerializer[User]):
    auth_providers = serializers.SerializerMethodField()
    assessment_status = serializers.SerializerMethodField()
    mfa_enabled = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="date_joined", read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "full_name",
            "role",
            "is_email_verified",
            "auth_providers",
            "assessment_status",
            "mfa_enabled",
            "created_at",
        ]
        read_only_fields = fields

    def get_mfa_enabled(self, obj: User) -> bool:
        from apps.accounts.mfa import mfa_enabled

        return mfa_enabled(obj)

    def get_auth_providers(self, obj: User) -> list[str]:
        providers: list[str] = []
        if obj.has_usable_password():
            providers.append("PASSWORD")
        if SocialAccount.objects.filter(user=obj, provider="google").exists():
            providers.append("GOOGLE")
        return providers

    def get_assessment_status(self, obj: User) -> str | None:
        if obj.role != "PATIENT":
            return None
        from apps.assessments.models import Assessment

        return (
            Assessment.objects.filter(user=obj)
            .order_by("-created_at")
            .values_list("status", flat=True)
            .first()
        )


class AuthTokensSerializer(serializers.Serializer[Any]):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class AuthResponseSerializer(serializers.Serializer[Any]):
    user = UserSummarySerializer(read_only=True)
    tokens = AuthTokensSerializer(read_only=True)


class GoogleAuthResponseSerializer(AuthResponseSerializer):
    is_new_user = serializers.BooleanField(read_only=True)


class RefreshRequestSerializer(StrictSerializer):
    refresh = serializers.CharField()


class StrictTokenRefreshSerializer(StrictFieldsMixin, TokenRefreshSerializer):
    pass


class RefreshResponseSerializer(serializers.Serializer[Any]):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class ConsentInputSerializer(StrictSerializer):
    document_type = serializers.ChoiceField(choices=ConsentRecord.DocumentType.choices)
    version = serializers.CharField(max_length=32)


class ConsentStatusSerializer(serializers.ModelSerializer[ConsentRecord]):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = ConsentRecord
        fields = ["document_type", "version", "accepted_at", "is_current"]
        read_only_fields = fields

    def get_is_current(self, obj: ConsentRecord) -> bool:
        current = self.context.get("current_versions", {})
        return bool(current.get(obj.document_type) == obj.version)


class ConsentBatchSerializer(StrictSerializer):
    consents = ConsentInputSerializer(many=True, allow_empty=False)


class LegalDocumentSummarySerializer(serializers.ModelSerializer[LegalDocument]):
    class Meta:
        model = LegalDocument
        fields = ["document_type", "version", "title", "published_at"]
        read_only_fields = fields


class LegalDocumentDetailSerializer(LegalDocumentSummarySerializer):
    class Meta(LegalDocumentSummarySerializer.Meta):
        fields = [*LegalDocumentSummarySerializer.Meta.fields, "sections"]
        read_only_fields = fields


class RegisterSerializer(StrictSerializer):
    full_name = serializers.CharField(max_length=120, min_length=2)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)
    consents = ConsentInputSerializer(many=True, required=False, default=list)

    def validate_full_name(self, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 2 or len(trimmed) > 120 or not _NAME_RE.search(trimmed):
            raise serializers.ValidationError("Enter a valid full name.")
        return trimmed

    def validate_email(self, value: str) -> str:
        return value.strip().lower()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        candidate = User(email=attrs["email"], full_name=attrs["full_name"])
        try:
            validate_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        if not attrs.get("consents"):
            raise ConsentRequiredInput()
        try:
            ConsentService.validate_items(attrs["consents"])
        except ConsentRequiredInput as exc:
            raise exc
        return attrs


class LoginSerializer(StrictSerializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        request = self.context.get("request")
        django_request = getattr(request, "_request", request)
        email = attrs["email"].strip().lower()
        try:
            user = authenticate(request=django_request, username=email, password=attrs["password"])
        except DjangoPermissionDenied as exc:
            raise AccountLocked() from exc
        if user is None:
            from apps.accounts.models import User as UserModel

            candidate = UserModel.objects.filter(email=email).first()
            if candidate is not None and not candidate.is_active:
                raise AccountDisabled()
            raise InvalidCredentials()
        self.user = user
        return attrs


class GoogleLoginSerializer(StrictSerializer):
    id_token = serializers.CharField()
    consents = ConsentInputSerializer(many=True, required=False)


class LogoutSerializer(StrictSerializer):
    refresh = serializers.CharField()


class VerifyEmailSerializer(StrictSerializer):
    token = serializers.CharField()


class EmptyBodySerializer(StrictSerializer):
    """Reject unexpected fields on endpoints that take no request body."""

    pass


class PasswordResetRequestSerializer(StrictSerializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class PasswordResetConfirmSerializer(StrictSerializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        user, status = parse_password_reset_token(attrs["token"])
        if user is None or status not in ("valid", EXPIRED):
            raise TokenInvalidInput()
        if status == EXPIRED:
            raise TokenExpiredInput()
        try:
            validate_password(attrs["password"], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        self.user = user
        return attrs


class ChangePasswordSerializer(StrictSerializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        user = self.context["request"].user
        try:
            validate_password(attrs["new_password"], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)}) from exc
        return attrs


class MeSerializer(UserSummarySerializer):
    pass


def token_payload(user: User) -> dict[str, Any]:
    tokens = issue_tokens(user)
    return {
        "user": UserSummarySerializer(user, context={"request": None}).data,
        "tokens": tokens,
    }


def auth_response(user: User, *, is_new_user: bool | None = None) -> dict[str, Any]:
    from apps.accounts.mfa import create_challenge, mfa_enabled

    if mfa_enabled(user) and not is_new_user:
        return create_challenge(user)
    payload = token_payload(user)
    if is_new_user is not None:
        payload["is_new_user"] = is_new_user
    return payload


__all__ = [
    "AuthResponseSerializer",
    "AuthTokensSerializer",
    "ChangePasswordSerializer",
    "ConsentBatchSerializer",
    "ConsentInputSerializer",
    "ConsentStatusSerializer",
    "EmptyBodySerializer",
    "GoogleAuthResponseSerializer",
    "GoogleLoginSerializer",
    "LegalDocumentDetailSerializer",
    "LegalDocumentSummarySerializer",
    "LoginSerializer",
    "LogoutSerializer",
    "MeSerializer",
    "PasswordResetConfirmSerializer",
    "PasswordResetRequestSerializer",
    "RefreshRequestSerializer",
    "RefreshResponseSerializer",
    "RegisterSerializer",
    "StrictTokenRefreshSerializer",
    "UserSummarySerializer",
    "VerifyEmailSerializer",
    "auth_response",
    "token_payload",
]


class MfaChallengeSerializer(serializers.Serializer[Any]):
    mfa_required = serializers.BooleanField()
    mfa_token = serializers.CharField()
    methods = serializers.ListField(child=serializers.CharField())


class MfaVerifySerializer(StrictSerializer):
    mfa_token = serializers.CharField(max_length=200)
    code = serializers.CharField(max_length=20)


class MfaCodeSerializer(StrictSerializer):
    code = serializers.CharField(max_length=20)


class MfaDisableSerializer(StrictSerializer):
    password = serializers.CharField(trim_whitespace=False)
    code = serializers.CharField(max_length=20)


class MfaSetupSerializer(serializers.Serializer[Any]):
    secret = serializers.CharField()
    otpauth_uri = serializers.CharField()


class MfaBackupCodesSerializer(serializers.Serializer[Any]):
    backup_codes = serializers.ListField(child=serializers.CharField())
