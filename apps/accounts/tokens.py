"""Signed tokens for email verification and password reset."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Stateless, expiring token bound to the user's email and password.

    ``last_login`` is deliberately excluded so logging in before verifying the
    address does not invalidate the link. Changing the password does.
    """

    key_salt = "apps.accounts.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user: Any, timestamp: int) -> str:
        return f"{user.pk}{user.email}{user.is_email_verified}{timestamp}{user.password}"

    def check_token(self, user: Any, token: str | None) -> bool:
        if not user or not token or user.is_email_verified:
            return False
        try:
            timestamp = base36_to_int(token.split("-", 1)[0])
        except (TypeError, ValueError):
            return False
        if self._num_seconds(self._now()) - timestamp > settings.EMAIL_VERIFICATION_TIMEOUT:
            return False
        return constant_time_compare(
            self._make_token_with_timestamp(user, timestamp, self.secret), token
        )


email_verification_token = EmailVerificationTokenGenerator()
