"""Identity, legal-document and consent business rules."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, cast

import structlog
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import get_adapter as get_social_adapter
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.enums import Role
from apps.accounts.models import ConsentRecord, LegalDocument, User
from apps.accounts.selectors import get_active_user_by_email, get_consents_for_user
from apps.accounts.tokens import (
    EXPIRED,
    decode_uid,
    encode_uid,
    make_email_verification_token,
    make_password_reset_token,
    parse_email_verification_token,
    parse_password_reset_token,
)
from apps.core.exceptions import (
    AccountDisabled,
    Conflict,
    ConsentRequiredInput,
    TokenExpiredInput,
    TokenInvalidInput,
)

logger = structlog.get_logger(__name__)


def _client_ip(request: HttpRequest) -> str | None:
    raw = request.META.get("REMOTE_ADDR")
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


class LegalDocumentService:
    @staticmethod
    def current(document_type: str) -> LegalDocument | None:
        return LegalDocument.objects.filter(document_type=document_type, is_current=True).first()

    @classmethod
    def current_map(cls) -> dict[str, LegalDocument]:
        return {
            document.document_type: document
            for document in LegalDocument.objects.filter(is_current=True)
        }

    @classmethod
    def current_versions(cls) -> dict[str, str]:
        return {
            document_type: document.version for document_type, document in cls.current_map().items()
        }


class ConsentService:
    REQUIRED = (
        ConsentRecord.DocumentType.TERMS_OF_SERVICE,
        ConsentRecord.DocumentType.PRIVACY_POLICY,
    )

    @classmethod
    def validate_items(
        cls, items: list[dict[str, Any]] | None, *, require_required: bool = True
    ) -> dict[str, LegalDocument]:
        current = LegalDocumentService.current_map()
        if not items:
            raise ConsentRequiredInput()
        seen: dict[str, LegalDocument] = {}
        for item in items:
            document_type = item["document_type"]
            version = item["version"]
            document = current.get(document_type)
            if document is None or document.version != version:
                raise ConsentRequiredInput()
            seen[document_type] = document
        if require_required and any(required not in seen for required in cls.REQUIRED):
            raise ConsentRequiredInput()
        return seen

    @classmethod
    def record_many(
        cls,
        *,
        user: User,
        items: list[dict[str, Any]],
        request: HttpRequest,
        require_required: bool = True,
    ) -> list[ConsentRecord]:
        documents = cls.validate_items(items, require_required=require_required)
        records: list[ConsentRecord] = []
        for document_type, document in documents.items():
            record, _created = ConsentRecord.objects.get_or_create(
                user=user,
                document_type=document_type,
                version=document.version,
                defaults={
                    "ip_address": _client_ip(request),
                    "user_agent": request.META.get("HTTP_USER_AGENT", "")[:1024],
                },
            )
            records.append(record)
        return records

    @classmethod
    def record(
        cls,
        *,
        user: User,
        document_type: str,
        version: str,
        request: HttpRequest,
    ) -> ConsentRecord:
        cls.record_many(
            user=user,
            items=[{"document_type": document_type, "version": version}],
            request=request,
        )
        return cast(
            ConsentRecord,
            get_consents_for_user(user)
            .filter(document_type=document_type, version=version)
            .first(),
        )

    @classmethod
    def has_current_consents(cls, user: User) -> bool:
        current = LegalDocumentService.current_versions()
        if not all(document_type in current for document_type in cls.REQUIRED):
            return False
        accepted = set(
            ConsentRecord.objects.filter(
                user=user,
                document_type__in=cls.REQUIRED,
            ).values_list("document_type", "version")
        )
        return all(
            (document_type, current[document_type]) in accepted for document_type in cls.REQUIRED
        )


@dataclass(frozen=True)
class RegistrationResult:
    user: User | None
    created: bool


class RegistrationService:
    @classmethod
    def register(
        cls,
        *,
        full_name: str,
        email: str,
        password: str,
        consents: list[dict[str, Any]],
        request: HttpRequest,
    ) -> RegistrationResult:
        normalized = email.strip().lower()
        ConsentService.validate_items(consents)
        with transaction.atomic():
            existing = User.objects.select_for_update().filter(email=normalized).first()
            if existing is not None:
                cls._notify_existing(existing)
                return RegistrationResult(user=None, created=False)
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        email=normalized,
                        password=password,
                        full_name=full_name.strip(),
                        role=Role.PATIENT,
                    )
            except IntegrityError:
                existing = User.objects.filter(email=normalized).first()
                if existing is not None:
                    cls._notify_existing(existing)
                return RegistrationResult(user=None, created=False)

            ConsentService.record_many(user=user, items=consents, request=request)
            transaction.on_commit(lambda: _send_verification(str(user.pk)))
            return RegistrationResult(user=user, created=True)

    @staticmethod
    def _notify_existing(user: User) -> None:
        transaction.on_commit(lambda: _send_account_exists(str(user.pk)))


def _send_verification(user_id: str) -> None:
    from apps.accounts.tasks import send_verification_email

    send_verification_email.delay(user_id)


def _send_account_exists(user_id: str) -> None:
    from apps.accounts.tasks import send_account_exists_email

    send_account_exists_email.delay(user_id)


class EmailVerificationService:
    @staticmethod
    def verify(*, token: str) -> User:
        user, status = parse_email_verification_token(token)
        if user is None or status == "invalid":
            raise TokenInvalidInput()
        if status == EXPIRED:
            raise TokenExpiredInput()
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
            raise Conflict("Email is already verified.")
        transaction.on_commit(lambda: _send_verification(str(user.pk)))


class PasswordResetService:
    @staticmethod
    def request(*, email: str) -> None:
        user = get_active_user_by_email(email)
        if user is None:
            logger.info("password_reset_unknown_email")
            return
        transaction.on_commit(lambda: _send_password_reset(str(user.pk)))

    @staticmethod
    def confirm(*, token: str, password: str) -> User:
        user, status = parse_password_reset_token(token)
        if user is None or status == "invalid":
            raise TokenInvalidInput()
        if status == EXPIRED:
            raise TokenExpiredInput()
        with transaction.atomic():
            user.set_password(password)
            user.save(update_fields=["password"])
            revoke_all_refresh_tokens(user)
        return user

    @staticmethod
    def change(*, user: User, current_password: str, new_password: str) -> User:
        if not user.has_usable_password():
            raise DRFValidationError({"current_password": ["This account uses Google sign-in."]})
        if not user.check_password(current_password):
            raise DRFValidationError({"current_password": ["Current password is incorrect."]})
        with transaction.atomic():
            user.set_password(new_password)
            user.save(update_fields=["password"])
        return user


def _send_password_reset(user_id: str) -> None:
    from apps.accounts.tasks import send_password_reset_email

    send_password_reset_email.delay(user_id)


class GoogleLoginError(ValidationError):
    pass


@dataclass(frozen=True)
class GoogleLoginResult:
    user: User
    is_new_user: bool


class GoogleLoginService:
    @staticmethod
    def login(
        *,
        request: HttpRequest,
        id_token: str,
        consents: list[dict[str, Any]] | None = None,
    ) -> GoogleLoginResult:
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

        is_new = not login.is_existing
        if is_new:
            ConsentService.validate_items(consents)

        social_saved = False
        with transaction.atomic():
            if login.is_existing:
                user = cast(User, login.user)
            else:
                user = cast(User, login.user)
                if user is None:
                    raise GoogleLoginError("Could not create an account from Google.")
                if not user.full_name:
                    user.full_name = (
                        f"{user.first_name} {user.last_name}".strip() or user.email.split("@", 1)[0]
                    )
                user.role = Role.PATIENT
                user.is_email_verified = True
                user.email_verified_at = timezone.now()
                user.set_unusable_password()
                user.save()
                login.save(request)
                social_saved = True
                ConsentService.record_many(user=user, items=consents or [], request=request)

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
        return GoogleLoginResult(user=user, is_new_user=is_new)


def issue_tokens(user: User) -> dict[str, str]:
    from django.contrib.auth.models import update_last_login

    update_last_login(User, user)
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def revoke_all_refresh_tokens(user: User) -> int:
    revoked = 0
    for outstanding in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=outstanding)
        if created:
            revoked += 1
    return revoked


__all__ = [
    "AccountDisabled",
    "ConsentService",
    "EmailVerificationService",
    "GoogleLoginError",
    "GoogleLoginResult",
    "GoogleLoginService",
    "LegalDocumentService",
    "PasswordResetService",
    "RegistrationResult",
    "RegistrationService",
    "decode_uid",
    "encode_uid",
    "issue_tokens",
    "make_email_verification_token",
    "make_password_reset_token",
    "revoke_all_refresh_tokens",
]
