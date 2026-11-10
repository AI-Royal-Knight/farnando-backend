"""JWT authentication with the contract's token error codes."""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import Token


class ContractJWTAuthentication(JWTAuthentication):
    def authenticate(self, request: Any) -> Any:
        try:
            return super().authenticate(request)
        except AuthenticationFailed as exc:
            from apps.core.exceptions import TokenInvalid

            raise TokenInvalid() from exc

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
