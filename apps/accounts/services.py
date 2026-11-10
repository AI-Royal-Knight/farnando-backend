"""Account business rules: registration, verification, reset and social login.

Views and serializers never mutate identity state directly; everything flows
through this module so audit, consent and token revocation cannot be skipped.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, cast

import structlog
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import get_adapter as get_social_adapter
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.enums import Role
from apps.accounts.models import ConsentRecord, User
from apps.accounts.selectors import get_active_user_by_email, get_active_user_by_id
from apps.accounts.tokens import email_verification_token

logger = structlog.get_logger(__name__)


def encode_uid(user_id: Any) -> str:
    return urlsafe_base64_encode(force_bytes(str(user_id)))


def decode_uid(value: str) -> str:
    try:
        return force_str(urlsafe_base64_decode(value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError("Invalid link.") from exc


def _client_ip(request: HttpRequest) -> str | None:
    raw = request.META.get("REMOTE_ADDR")
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


class ConsentService:
    @staticmethod
    def current_version(document_type: str) -> str:
        versions = getattr(settings, "CONSENT_VERSIONS", {})
        try:
            return str(versions[document_type])
        except KeyError as exc:
            raise RuntimeError(f"Missing consent version for {document_type}") from exc

    @classmethod
    def record(
        cls,
        *,
        user: User,
        document_type: str,
        request: HttpRequest,
        version: str | None = None,
    ) -> ConsentRecord:
        if document_type not in ConsentRecord.DocumentType.values:
            raise ValidationError({"document_type": "Unknown consent document."})
        current = cls.current_version(document_type)
        if version is not None and version != current:
            raise ValidationError(
                {
                    "version": (
                        "The consent document has changed. Reload the page and accept "
                        "the current version."
                    ),
                    "current_version": current,
                }
            )
        record, _created = ConsentRecord.objects.get_or_create(
            user=user,
            document_type=document_type,
            version=current,
            defaults={
                "ip_address": _client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:1024],
            },
        )
        return record

    @classmethod
    def record_registration_consents(cls, *, user: User, request: HttpRequest) -> None:
        for document_type in (
            ConsentRecord.DocumentType.TERMS,
            ConsentRecord.DocumentType.PRIVACY,
        ):
            cls.record(user=user, document_type=document_type, request=request)


@dataclass(frozen=True)
class RegistrationResult:
    created: bool
    user: User | None


class RegistrationService:
    """Create a patient account without leaking whether an email exists.

    The endpoint always returns the same response. Existing verified accounts
    receive a "you already have an account" email; existing unverified accounts
    receive a fresh verification link.
    """

    @classmethod
    def register(
        cls,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        request: HttpRequest,
    ) -> RegistrationResult:
        normalized = email.strip().lower()
        with transaction.atomic():
            existing = User.objects.select_for_update().filter(email=normalized).first()
            if existing is not None:
                cls._notify_existing(existing)
                return RegistrationResult(created=False, user=None)
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        email=normalized,
                        password=password,
                        first_name=first_name.strip(),
                        last_name=last_name.strip(),
                        role=Role.PATIENT,
                    )
            except IntegrityError:
                # Concurrent registration won the race; behave like an existing user.
                existing = User.objects.filter(email=normalized).first()
                if existing is not None:
                    cls._notify_existing(existing)
                return RegistrationResult(created=False, user=None)

            ConsentService.record_registration_consents(user=user, request=request)
            transaction.on_commit(lambda: _send_verification(str(user.pk)))
            return RegistrationResult(created=True, user=user)

    @staticmethod
    def _notify_existing(user: User) -> None:
        if user.is_email_verified:
            transaction.on_commit(lambda: _send_account_exists(str(user.pk)))
        else:
            transaction.on_commit(lambda: _send_verification(str(user.pk)))


def _send_verification(user_id: str) -> None:
    from apps.accounts.tasks import send_verification_email

    send_verification_email.delay(user_id)


def _send_account_exists(user_id: str) -> None:
    from apps.accounts.tasks import send_account_exists_email

    send_account_exists_email.delay(user_id)


class EmailVerificationService:
    @staticmethod
    def verify(*, uid: str, token: str) -> User:
        user_id = decode_uid(uid)
        user = get_active_user_by_id(user_id)
        if user is None or not email_verification_token.check_token(user, token):
            raise ValidationError({"token": "This verification link is invalid or has expired."})
        if not user.is_email_verified:
            with transaction.atomic():
                user.is_email_verified = True
                user.email_verified_at = timezone.now()
                user.save(update_fields=["is_email_verified", "email_verified_at"])
                EmailAddress.objects.update_or_create(
                    user=user,
                    email=user.email,
                    defaults={"verified": True, "primary": True},
                )
        return user

    @staticmethod
    def resend(*, user: User) -> None:
        if user.is_email_verified:
            raise ValidationError({"detail": "Email is already verified."})
        transaction.on_commit(lambda: _send_verification(str(user.pk)))


class PasswordResetService:
    @staticmethod
    def request(*, email: str) -> None:
        user = get_active_user_by_email(email)
        if user is None:
            # Same response and no timing signal: the send happens in a worker.
            logger.info("password_reset_unknown_email")
            return
        transaction.on_commit(lambda: _send_password_reset(str(user.pk)))

    @staticmethod
    def confirm(*, uid: str, token: str, new_password: str) -> User:
        user_id = decode_uid(uid)
        user = get_active_user_by_id(user_id)
        if user is None or not default_token_generator.check_token(user, token):
            raise ValidationError({"token": "This reset link is invalid or has expired."})
        with transaction.atomic():
            user.set_password(new_password)
            user.save(update_fields=["password"])
            revoke_all_refresh_tokens(user)
        return user


def _send_password_reset(user_id: str) -> None:
    from apps.accounts.tasks import send_password_reset_email

    send_password_reset_email.delay(user_id)


class GoogleLoginError(ValidationError):
    pass


class GoogleLoginService:
    @staticmethod
    def login(*, request: HttpRequest, id_token: str) -> User:
        if not settings.GOOGLE_CLIENT_ID:
            raise GoogleLoginError("Google sign-in is not configured.")

        adapter = get_social_adapter()
        try:
            provider = adapter.get_provider(request, "google")
            login = provider.verify_token(request, {"id_token": id_token})
        except Exception as exc:
            logger.warning("google_login_failed", error=type(exc).__name__)
            raise GoogleLoginError("Invalid Google credential.") from exc

        login.lookup()
        verified = any(getattr(address, "verified", False) for address in login.email_addresses)
        if not verified:
            verified = bool(login.account.extra_data.get("email_verified", False))
        if not verified:
            raise GoogleLoginError("Google did not verify this email address.")

        social_saved = False
        with transaction.atomic():
            if login.is_existing:
                user = cast(User, login.user)
            else:
                user = cast(User, login.user)
                if user is None:
                    raise GoogleLoginError("Could not create an account from Google.")
                user.role = Role.PATIENT
                user.is_email_verified = True
                user.email_verified_at = timezone.now()
                user.set_unusable_password()
                user.save()
                login.save(request)
                social_saved = True

            if not user.is_email_verified:
                user.is_email_verified = True
                user.email_verified_at = timezone.now()
                user.save(update_fields=["is_email_verified", "email_verified_at"])
            if not EmailAddress.objects.filter(user=user, email=user.email).exists():
                EmailAddress.objects.update_or_create(
                    user=user,
                    email=user.email,
                    defaults={"verified": True, "primary": True},
                )
            if login.account.pk is None and not social_saved:
                login.save(request)
        return user


def issue_tokens(user: User) -> dict[str, str]:
    from django.contrib.auth.models import update_last_login

    update_last_login(User, user)
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role
    refresh["email_verified"] = user.is_email_verified
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


def revoke_all_refresh_tokens(user: User) -> int:
    revoked = 0
    for outstanding in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=outstanding)
        if created:
            revoked += 1
    return revoked
