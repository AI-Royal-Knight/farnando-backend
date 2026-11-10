"""Input/output contracts for authentication and account lifecycle."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import ConsentRecord, User
from apps.accounts.services import decode_uid, issue_tokens


class UserSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "is_email_verified",
            "date_joined",
        ]
        read_only_fields = ["id", "email", "role", "is_email_verified", "date_joined"]


class ConsentSerializer(serializers.ModelSerializer[ConsentRecord]):
    document_type_label = serializers.CharField(source="get_document_type_display", read_only=True)

    class Meta:
        model = ConsentRecord
        fields = ["id", "document_type", "document_type_label", "version", "accepted_at"]
        read_only_fields = fields


class PaginatedConsentSerializer(serializers.Serializer[dict[str, Any]]):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = ConsentSerializer(many=True)


class MeSerializer(UserSerializer):
    consents = ConsentSerializer(many=True, read_only=True)

    class Meta(UserSerializer.Meta):
        fields = [*UserSerializer.Meta.fields, "consents"]


class MeUpdateSerializer(serializers.ModelSerializer[User]):
    class Meta:
        model = User
        fields = ["first_name", "last_name"]


class RegisterSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    accept_terms = serializers.BooleanField()
    accept_privacy = serializers.BooleanField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        if not attrs.get("accept_terms") or not attrs.get("accept_privacy"):
            raise serializers.ValidationError(
                {
                    "consents": (
                        "You must accept the Terms of Service and the Privacy Policy "
                        "to create an account."
                    )
                }
            )
        candidate = User(
            email=attrs["email"],
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs


class LoginSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        request = self.context.get("request")
        django_request = getattr(request, "_request", request)
        email = attrs["email"].strip().lower()
        try:
            user = authenticate(
                request=django_request,
                username=email,
                password=attrs["password"],
            )
        except DjangoPermissionDenied as exc:
            from apps.core.exceptions import AccountLocked

            raise AccountLocked() from exc
        if user is None:
            raise AuthenticationFailed("Invalid email or password.")
        if not user.is_active:
            raise AuthenticationFailed("Invalid email or password.")
        self.user = user
        return attrs


class TokenPairSerializer(serializers.Serializer[dict[str, Any]]):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    token_type = serializers.CharField(read_only=True, default="Bearer")
    expires_in = serializers.IntegerField(read_only=True, default=900)


class LoginResponseSerializer(TokenPairSerializer):
    user = UserSerializer(read_only=True)


class RefreshRequestSerializer(serializers.Serializer[dict[str, Any]]):
    refresh = serializers.CharField()


class LogoutSerializer(serializers.Serializer[dict[str, Any]]):
    refresh = serializers.CharField()


class VerifyEmailSerializer(serializers.Serializer[dict[str, Any]]):
    uid = serializers.CharField()
    token = serializers.CharField()


class ResendVerificationSerializer(serializers.Serializer[dict[str, Any]]):
    pass


class PasswordResetRequestSerializer(serializers.Serializer[dict[str, Any]]):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer[dict[str, Any]]):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "Passwords do not match."})
        try:
            user_id = decode_uid(attrs["uid"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"token": "This reset link is invalid or has expired."}
            ) from exc
        user = User.objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            raise serializers.ValidationError(
                {"token": "This reset link is invalid or has expired."}
            )
        try:
            validate_password(attrs["new_password"], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)}) from exc
        self.user = user
        return attrs


class GoogleLoginSerializer(serializers.Serializer[dict[str, Any]]):
    id_token = serializers.CharField()


class ConsentCreateSerializer(serializers.Serializer[dict[str, Any]]):
    document_type = serializers.ChoiceField(choices=ConsentRecord.DocumentType.choices)


def token_payload(user: User) -> dict[str, Any]:
    tokens = issue_tokens(user)
    return {
        **tokens,
        "token_type": "Bearer",
        "expires_in": int(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        "user": UserSerializer(user).data,
    }


__all__ = [
    "ConsentCreateSerializer",
    "ConsentSerializer",
    "GoogleLoginSerializer",
    "LoginResponseSerializer",
    "LoginSerializer",
    "LogoutSerializer",
    "MeSerializer",
    "MeUpdateSerializer",
    "PaginatedConsentSerializer",
    "PasswordResetConfirmSerializer",
    "PasswordResetRequestSerializer",
    "RefreshRequestSerializer",
    "RegisterSerializer",
    "ResendVerificationSerializer",
    "TokenPairSerializer",
    "UserSerializer",
    "VerifyEmailSerializer",
    "token_payload",
]
