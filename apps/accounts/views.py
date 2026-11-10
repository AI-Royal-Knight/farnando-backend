"""Thin authentication and consent endpoints."""

from __future__ import annotations

from typing import Any, cast

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.accounts.selectors import get_consents_for_user
from apps.accounts.serializers import (
    ConsentCreateSerializer,
    ConsentSerializer,
    GoogleLoginSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    LogoutSerializer,
    MeSerializer,
    MeUpdateSerializer,
    PaginatedConsentSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshRequestSerializer,
    RegisterSerializer,
    ResendVerificationSerializer,
    TokenPairSerializer,
    VerifyEmailSerializer,
    token_payload,
)
from apps.accounts.services import (
    ConsentService,
    EmailVerificationService,
    GoogleLoginError,
    GoogleLoginService,
    PasswordResetService,
    RegistrationService,
)
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.schema import COMMON_ERRORS
from apps.core.serializers import DetailSerializer

_LOGIN_EXAMPLES = [
    OpenApiExample(
        "Patient login",
        value={"email": "paciente@example.com", "password": "correct-horse-battery"},
        request_only=True,
    ),
    OpenApiExample(
        "Login response",
        value={
            "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "token_type": "Bearer",
            "expires_in": 900,
            "user": {
                "id": "6f1c2b7e-0d7e-4d6f-9c43-7a2b1f4c8e10",
                "email": "paciente@example.com",
                "first_name": "Ana",
                "last_name": "Quispe",
                "role": "PATIENT",
                "is_email_verified": True,
                "date_joined": "2026-11-10T14:00:00Z",
            },
        },
        response_only=True,
    ),
]


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Create a patient account",
        auth=[],
        description=(
            "Creates a patient account and sends a verification email. The response is "
            "identical whether or not the email already exists, preventing account "
            "enumeration. Terms and privacy consent are recorded with IP and user agent."
        ),
        request=RegisterSerializer,
        responses={202: DetailSerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample(
                "Registration",
                value={
                    "email": "paciente@example.com",
                    "password": "correct-horse-battery",
                    "password_confirm": "correct-horse-battery",
                    "first_name": "Ana",
                    "last_name": "Quispe",
                    "accept_terms": True,
                    "accept_privacy": True,
                },
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        RegistrationService.register(
            email=data["email"],
            password=data["password"],
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            request=request._request,
        )
        return Response(
            {
                "detail": (
                    "If the address can be used, a verification email is on its way. "
                    "Check your inbox to continue."
                )
            },
            status=status.HTTP_202_ACCEPTED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Log in with email and password",
        auth=[],
        description=(
            "Returns a 15-minute access token and a rotating 7-day refresh token. "
            "Invalid credentials, unknown accounts and inactive accounts all return "
            "the same error. Repeated failures are locked out by django-axes."
        ),
        request=LoginSerializer,
        responses={200: LoginResponseSerializer, **COMMON_ERRORS},
        examples=_LOGIN_EXAMPLES,
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.user
        return Response(token_payload(user))


class RefreshView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Rotate a refresh token",
        auth=[],
        description="Returns a new access/refresh pair and blacklists the submitted refresh token.",
        request=RefreshRequestSerializer,
        responses={200: TokenPairSerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample(
                "Refresh",
                value={"refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."},
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = TokenRefreshSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        return Response(serializer.validated_data)


class LogoutView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Log out and blacklist the refresh token",
        auth=[],
        description="Idempotent: an already blacklisted or malformed token still returns 204.",
        request=LogoutSerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "email_verification"

    @extend_schema(
        tags=["auth"],
        summary="Verify an email address",
        auth=[],
        request=VerifyEmailSerializer,
        responses={200: DetailSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            EmailVerificationService.verify(**serializer.validated_data)
        except DjangoValidationError as exc:
            raise ValidationError({"token": exc.messages[0]}) from exc
        return Response({"detail": "Email verified."})


class ResendVerificationView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "email_verification"

    @extend_schema(
        tags=["auth"],
        summary="Resend the verification email",
        request=None,
        responses={202: DetailSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            EmailVerificationService.resend(user=cast(User, request.user))
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages[0]}) from exc
        return Response(
            {"detail": "If the address is not yet verified, a new link is on its way."},
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    @extend_schema(
        tags=["auth"],
        summary="Request a password reset email",
        auth=[],
        description="Always returns 202; unknown addresses are not revealed.",
        request=PasswordResetRequestSerializer,
        responses={202: DetailSerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample(
                "Reset request",
                value={"email": "paciente@example.com"},
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        PasswordResetService.request(email=serializer.validated_data["email"])
        return Response(
            {"detail": "If the address belongs to an account, a reset email is on its way."},
            status=status.HTTP_202_ACCEPTED,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"

    @extend_schema(
        tags=["auth"],
        summary="Set a new password using a reset token",
        auth=[],
        request=PasswordResetConfirmSerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            PasswordResetService.confirm(
                uid=serializer.validated_data["uid"],
                token=serializer.validated_data["token"],
                new_password=serializer.validated_data["new_password"],
            )
        except DjangoValidationError as exc:
            raise ValidationError({"token": exc.messages[0]}) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class GoogleLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Log in with a Google ID token",
        auth=[],
        description=(
            "Verifies the Google credential with django-allauth, links or creates a "
            "patient account, and returns the same JWT pair as password login. The "
            "Google ID token must be obtained by the SPA from Google Identity Services."
        ),
        request=GoogleLoginSerializer,
        responses={200: LoginResponseSerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample(
                "Google credential",
                value={"id_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6..."},
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = GoogleLoginService.login(
                request=request._request,
                id_token=serializer.validated_data["id_token"],
            )
        except GoogleLoginError as exc:
            message = exc.messages[0] if exc.messages else "Invalid Google credential."
            raise ValidationError({"id_token": message}) from exc
        return Response(token_payload(user))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def _serialize(self, user: User) -> dict[str, Any]:
        return MeSerializer(user).data

    @extend_schema(
        tags=["accounts"],
        summary="Get the current user and consent history",
        responses={200: MeSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return Response(self._serialize(cast(User, request.user)))

    @extend_schema(
        tags=["accounts"],
        summary="Update the current user's display names",
        request=MeUpdateSerializer,
        responses={200: MeSerializer, **COMMON_ERRORS},
    )
    def patch(self, request: Request) -> Response:
        serializer = MeUpdateSerializer(cast(User, request.user), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(self._serialize(cast(User, request.user)))


class ConsentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["accounts"],
        summary="List the current user's consent records",
        responses={200: PaginatedConsentSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        records = get_consents_for_user(cast(User, request.user))
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(records, request, view=self)
        serializer = ConsentSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        tags=["accounts"],
        summary="Accept the current version of a consent document",
        description=(
            "The server records the current configured version; a client-supplied "
            "version is never trusted. Re-accepting the same version is idempotent."
        ),
        request=ConsentCreateSerializer,
        responses={201: ConsentSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = ConsentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            record = ConsentService.record(
                user=cast(User, request.user),
                document_type=serializer.validated_data["document_type"],
                request=request._request,
            )
        except DjangoValidationError as exc:
            raise ValidationError(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            ) from exc
        return Response(ConsentSerializer(record).data, status=status.HTTP_201_CREATED)


__all__ = [
    "ConsentListCreateView",
    "GoogleLoginView",
    "LoginView",
    "LogoutView",
    "MeView",
    "PasswordResetConfirmView",
    "PasswordResetRequestView",
    "RefreshView",
    "RegisterView",
    "ResendVerificationView",
    "VerifyEmailView",
]
