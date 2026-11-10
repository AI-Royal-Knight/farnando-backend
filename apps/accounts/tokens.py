"""Signed tokens for email verification and password reset.

The public contract sends a single opaque ``token`` field. Internally the user
id is prefixed to the Django token and both are validated before use. Email
verification is single-use: the first successful call sets
``is_email_verified`` and invalidates the token.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes, force_str
from django.utils.http import base36_to_int, urlsafe_base64_decode, urlsafe_base64_encode

from apps.accounts.models import User

VALID = "valid"
EXPIRED = "expired"
INVALID = "invalid"


def encode_uid(user_id: Any) -> str:
    return urlsafe_base64_encode(force_bytes(str(user_id)))


def decode_uid(value: str) -> str | None:
    try:
        return force_str(urlsafe_base64_decode(value))
    except (TypeError, ValueError, OverflowError):
        return None


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "apps.accounts.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user: Any, timestamp: int) -> str:
        return f"{user.pk}{user.email}{user.is_email_verified}{timestamp}{user.password}"

    def token_status(self, user: User, token: str | None) -> str:
        if not user or not token or user.is_email_verified:
            return INVALID
        try:
            timestamp = base36_to_int(token.split("-", 1)[0])
        except (TypeError, ValueError):
            return INVALID
        if self._num_seconds(self._now()) - timestamp > settings.EMAIL_VERIFICATION_TIMEOUT:
            return EXPIRED
        expected = self._make_token_with_timestamp(user, timestamp, self.secret)
        return VALID if constant_time_compare(expected, token) else INVALID

    def check_token(self, user: Any, token: str | None) -> bool:
        return self.token_status(user, token) == VALID


email_verification_token = EmailVerificationTokenGenerator()


def make_email_verification_token(user: User) -> str:
    return f"{encode_uid(user.pk)}.{email_verification_token.make_token(user)}"


def parse_email_verification_token(value: str) -> tuple[User | None, str]:
    try:
        uid, token = value.split(".", 1)
    except (AttributeError, ValueError):
        return None, INVALID
    user_id = decode_uid(uid)
    if user_id is None:
        return None, INVALID
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None, INVALID
    return user, email_verification_token.token_status(user, token)


def make_password_reset_token(user: User) -> str:
    return f"{encode_uid(user.pk)}.{default_token_generator.make_token(user)}"


def password_reset_token_status(user: User, token: str) -> str:
    if not user or not token:
        return INVALID
    try:
        timestamp = base36_to_int(token.split("-", 1)[0])
    except (TypeError, ValueError):
        return INVALID
    now = default_token_generator._num_seconds(default_token_generator._now())
    if now - timestamp > settings.PASSWORD_RESET_TIMEOUT:
        return EXPIRED
    return VALID if default_token_generator.check_token(user, token) else INVALID


def parse_password_reset_token(value: str) -> tuple[User | None, str]:
    try:
        uid, token = value.split(".", 1)
    except (AttributeError, ValueError):
        return None, INVALID
    user_id = decode_uid(uid)
    if user_id is None:
        return None, INVALID
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return None, INVALID
    return user, password_reset_token_status(user, token)
