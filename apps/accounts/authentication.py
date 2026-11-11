"""JWT authentication with the contract's token error codes."""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import Token

MFA_ENROLLMENT_PATHS = (
    "/api/v1/auth/me/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/mfa/totp/setup/",
    "/api/v1/auth/mfa/totp/confirm/",
)


class ContractJWTAuthentication(JWTAuthentication):
    def authenticate(self, request: Any) -> Any:
        try:
            result = super().authenticate(request)
        except AuthenticationFailed as exc:
            from apps.core.exceptions import TokenInvalid

            raise TokenInvalid() from exc
        if result is not None:
            self._enforce_mfa_enrollment(request, result[0])
        return result

    @staticmethod
    def _enforce_mfa_enrollment(request: Any, user: Any) -> None:
        """Roles that must use MFA get enrollment-only access until they enrol."""

        from apps.accounts.mfa import mfa_enabled, mfa_required

        if not mfa_required(user) or request.path in MFA_ENROLLMENT_PATHS:
            return
        if not mfa_enabled(user):
            from apps.core.exceptions import MfaEnrollmentRequired

            raise MfaEnrollmentRequired()

    def get_validated_token(self, raw_token: bytes) -> Token:
        try:
            return super().get_validated_token(raw_token)
        except InvalidToken as exc:
            from apps.core.exceptions import TokenExpired, TokenInvalid

            detail: Any = exc.detail
            messages = detail.get("messages", []) if isinstance(detail, dict) else []
            if any("expired" in str(item.get("message", "")).lower() for item in messages):
                raise TokenExpired() from exc
            raise TokenInvalid() from exc
