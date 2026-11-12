"""Thin authentication, legal-document and consent endpoints."""

from __future__ import annotations

from typing import Any, cast

from django.http import Http404
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import ConsentRecord, LegalDocument, User
from apps.accounts.selectors import get_consents_for_user
from apps.accounts.serializers import (
    AuthResponseSerializer,
    ChangePasswordSerializer,
    ConsentBatchSerializer,
    ConsentStatusSerializer,
    EmptyBodySerializer,
    GoogleAuthResponseSerializer,
    GoogleLoginSerializer,
    LegalDocumentDetailSerializer,
    LegalDocumentSummarySerializer,
    LoginSerializer,
    LogoutSerializer,
    MfaBackupCodesSerializer,
    MfaCodeSerializer,
    MfaDisableSerializer,
    MfaSetupSerializer,
    MfaVerifySerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshRequestSerializer,
    RefreshResponseSerializer,
    RegisterSerializer,
    StrictTokenRefreshSerializer,
    UserSummarySerializer,
    VerifyEmailSerializer,
    auth_response,
)
from apps.accounts.services import (
    ConsentService,
    EmailVerificationService,
    GoogleLoginError,
    GoogleLoginService,
    LegalDocumentService,
    PasswordResetService,
    RegistrationService,
)
from apps.accounts.throttles import PasswordResetEmailThrottle
from apps.core.audit import record_audit
from apps.core.exceptions import (
    AccountDisabled,
    ConsentRequiredInput,
    TokenInvalid,
)
from apps.core.permissions import IsAuthenticatedAndActive
from apps.core.schema import COMMON_ERRORS

_LOGIN_EXAMPLES = [
    OpenApiExample(
        "Patient login",
        value={"email": "paciente@example.com", "password": "correct-horse-battery"},
        request_only=True,
    ),
    OpenApiExample(
        "Login response",
        value={
            "user": {
                "id": "6f1c2b7e-0d7e-4d6f-9c43-7a2b1f4c8e10",
                "email": "paciente@example.com",
                "full_name": "Ana Quispe",
                "role": "PATIENT",
                "is_email_verified": True,
                "auth_providers": ["PASSWORD"],
                "assessment_status": None,
                "created_at": "2026-11-10T14:00:00Z",
            },
            "tokens": {"access": "eyJ...", "refresh": "eyJ..."},
        },
        response_only=True,
    ),
]

_DOC_TYPE_BY_SLUG = {
    "terms-of-service": ConsentRecord.DocumentType.TERMS_OF_SERVICE,
    "privacy-policy": ConsentRecord.DocumentType.PRIVACY_POLICY,
    "health-data-processing": ConsentRecord.DocumentType.HEALTH_DATA_PROCESSING,
}


def _public_cache(response: Response) -> Response:
    response["Cache-Control"] = "public, max-age=300"
    return response


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Create a patient account",
        auth=[],
        request=RegisterSerializer,
        responses={201: AuthResponseSerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample(
                "Registration",
                value={
                    "full_name": "Maria Gonzalez",
                    "email": "maria@example.com",
                    "password": "A-very-l0ng-password!",
                    "confirm_password": "A-very-l0ng-password!",
                    "consents": [
                        {"document_type": "TERMS_OF_SERVICE", "version": "2026-11-01"},
                        {"document_type": "PRIVACY_POLICY", "version": "2026-11-01"},
                    ],
                },
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = RegistrationService.register(
            full_name=data["full_name"],
            email=data["email"],
            password=data["password"],
            consents=data["consents"],
            request=request._request,
        )
        if not result.created or result.user is None:
            raise ValidationError({"email": ["Unable to create an account with this email."]})
        return Response(auth_response(result.user), status=status.HTTP_201_CREATED)


def _logged_in(user: User, request: Request, body: dict[str, Any], method: str) -> dict[str, Any]:
    """Record a LOGIN audit entry once tokens are issued (deviation 129)."""

    if "tokens" in body:
        record_audit(
            actor=user,
            action="auth.login",
            object_type="User",
            object_id=user.pk,
            request=request,
            metadata={"method": method},
        )
    return body


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Log in with email and password",
        description=(
            "When the account has MFA enabled the response is "
            '`{mfa_required: true, mfa_token, methods: ["TOTP"]}` instead of tokens; '
            "finish with `POST /auth/mfa/verify/`."
        ),
        auth=[],
        request=LoginSerializer,
        responses={200: AuthResponseSerializer, **COMMON_ERRORS},
        examples=_LOGIN_EXAMPLES,
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(
            _logged_in(serializer.user, request, auth_response(serializer.user), "PASSWORD")
        )


class RefreshView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Rotate a refresh token",
        auth=[],
        request=RefreshRequestSerializer,
        responses={200: RefreshResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = StrictTokenRefreshSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise TokenInvalid() from exc
        return Response(serializer.validated_data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticatedAndActive]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Log out and blacklist the refresh token",
        request=LogoutSerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            if str(token["user_id"]) == str(cast(User, request.user).pk):
                token.blacklist()
        except (TokenError, KeyError):
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
        responses={200: UserSummarySerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = EmailVerificationService.verify(token=serializer.validated_data["token"])
        return Response({"user": UserSummarySerializer(user).data})


class ResendVerificationView(APIView):
    permission_classes = [IsAuthenticatedAndActive]
    throttle_scope = "email_verification"

    @extend_schema(
        tags=["auth"],
        summary="Resend the verification email",
        request=None,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = EmptyBodySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        EmailVerificationService.resend(user=cast(User, request.user))
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"
    throttle_classes = [ScopedRateThrottle, PasswordResetEmailThrottle]

    @extend_schema(
        tags=["auth"],
        summary="Request a password reset email",
        auth=[],
        request=PasswordResetRequestSerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        PasswordResetService.request(email=serializer.validated_data["email"])
        return Response(status=status.HTTP_204_NO_CONTENT)


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
        PasswordResetService.confirm(
            token=serializer.validated_data["token"],
            password=serializer.validated_data["password"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class GoogleLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Log in with a Google ID token",
        auth=[],
        request=GoogleLoginSerializer,
        responses={
            200: GoogleAuthResponseSerializer,
            201: GoogleAuthResponseSerializer,
            **COMMON_ERRORS,
        },
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
            result = GoogleLoginService.login(
                request=request._request,
                id_token=serializer.validated_data["id_token"],
                consents=serializer.validated_data.get("consents"),
            )
        except GoogleLoginError as exc:
            raise ValidationError({"id_token": exc.messages[0]}) from exc
        if not result.user.is_active:
            # Suspended or anonymised accounts must not regain access via Google.
            raise AccountDisabled()
        body = auth_response(result.user, is_new_user=result.is_new_user)
        return Response(
            _logged_in(result.user, request, body, "GOOGLE"),
            status=status.HTTP_201_CREATED if result.is_new_user else status.HTTP_200_OK,
        )


class MeView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["accounts"],
        summary="Get the current user",
        responses={200: UserSummarySerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return Response(UserSummarySerializer(cast(User, request.user)).data)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["auth"],
        summary="Change the current password",
        request=ChangePasswordSerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        PasswordResetService.change(
            user=cast(User, request.user),
            current_password=serializer.validated_data["current_password"],
            new_password=serializer.validated_data["new_password"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class LegalDocumentListView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["legal"],
        summary="List current legal documents",
        auth=[],
        responses={200: LegalDocumentSummarySerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        documents = LegalDocument.objects.filter(is_current=True).order_by("document_type")
        return _public_cache(Response(LegalDocumentSummarySerializer(documents, many=True).data))


class LegalDocumentDetailView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["legal"],
        summary="Read the current version of a legal document",
        auth=[],
        responses={200: LegalDocumentDetailSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, document_type: str) -> Response:
        enum_value = _DOC_TYPE_BY_SLUG.get(document_type)
        if enum_value is None:
            raise Http404
        document = LegalDocumentService.current(enum_value)
        if document is None:
            raise Http404
        return _public_cache(Response(LegalDocumentDetailSerializer(document).data))


class ConsentMeView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["consents"],
        summary="List the current user's consent history",
        responses={200: ConsentStatusSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        records = get_consents_for_user(cast(User, request.user))
        current = LegalDocumentService.current_versions()
        return Response(
            ConsentStatusSerializer(records, many=True, context={"current_versions": current}).data
        )


class ConsentCreateView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["consents"],
        summary="Accept one or more current legal-document versions",
        request=ConsentBatchSerializer,
        responses={201: ConsentStatusSerializer(many=True), **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = ConsentBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            ConsentService.record_many(
                user=cast(User, request.user),
                items=serializer.validated_data["consents"],
                request=request._request,
                require_required=False,
            )
        except ConsentRequiredInput as exc:
            raise ValidationError(
                {"consents": ["One or more document versions are not current."]}
            ) from exc
        records = get_consents_for_user(cast(User, request.user))
        current = LegalDocumentService.current_versions()
        return Response(
            ConsentStatusSerializer(records, many=True, context={"current_versions": current}).data,
            status=status.HTTP_201_CREATED,
        )


__all__ = [
    "ChangePasswordView",
    "ConsentCreateView",
    "ConsentMeView",
    "GoogleLoginView",
    "LegalDocumentDetailView",
    "LegalDocumentListView",
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


class MfaVerifyView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Finish an MFA login with a TOTP or backup code",
        auth=[],
        request=MfaVerifySerializer,
        responses={200: AuthResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        from apps.accounts.mfa import verify_challenge
        from apps.accounts.serializers import token_payload

        serializer = MfaVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_challenge(
            serializer.validated_data["mfa_token"], serializer.validated_data["code"]
        )
        if not user.is_active:
            raise AccountDisabled()
        return Response(_logged_in(user, request, token_payload(user), "MFA"))


class MfaSetupView(APIView):
    permission_classes = [IsAuthenticatedAndActive]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Start TOTP enrollment",
        request=None,
        responses={200: MfaSetupSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        from apps.accounts.mfa import start_setup

        return Response(start_setup(cast(User, request.user)))


class MfaConfirmView(APIView):
    permission_classes = [IsAuthenticatedAndActive]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Confirm TOTP enrollment; returns backup codes once",
        request=MfaCodeSerializer,
        responses={200: MfaBackupCodesSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        from apps.accounts.mfa import confirm_setup

        serializer = MfaCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        codes = confirm_setup(cast(User, request.user), serializer.validated_data["code"])
        return Response({"backup_codes": codes})


class MfaDisableView(APIView):
    permission_classes = [IsAuthenticatedAndActive]
    throttle_scope = "auth"

    @extend_schema(
        tags=["auth"],
        summary="Disable TOTP (not allowed for clinical roles)",
        request=MfaDisableSerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        from apps.accounts.mfa import disable

        serializer = MfaDisableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        disable(
            cast(User, request.user),
            password=serializer.validated_data["password"],
            code=serializer.validated_data["code"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
