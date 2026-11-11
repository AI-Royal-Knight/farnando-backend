"""TOTP multi-factor authentication (contract Part 4, 2.5).

RFC 6238 with SHA-1, 30-second steps and 6 digits, accepting one step of clock
drift. A step that was already used cannot be replayed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import MfaBackupCode, MfaChallenge, MfaDevice, User
from apps.core.exceptions import ApplicationError, Conflict, TokenInvalid

STEP_SECONDS = 30
DIGITS = 6
BACKUP_CODE_COUNT = 10


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    padding = "=" * (-len(secret) % 8)
    return base64.b32decode(secret.upper() + padding)


def totp_at(secret: str, step: int) -> str:
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**DIGITS)
    return f"{code:0{DIGITS}d}"


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP_SECONDS)


def otpauth_uri(secret: str, email: str) -> str:
    issuer = str(settings.MFA_ISSUER)
    label = quote(f"{issuer}:{email}")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def mfa_enabled(user: Any) -> bool:
    return MfaDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def mfa_required(user: Any) -> bool:
    return getattr(user, "role", None) in set(settings.MFA_REQUIRED_ROLES)


def _match_totp(device: MfaDevice, code: str) -> bool:
    if not (code.isdigit() and len(code) == DIGITS):
        return False
    step = current_step()
    for candidate in (step, step - 1, step + 1):
        if device.last_used_step is not None and candidate <= device.last_used_step:
            continue
        if hmac.compare_digest(totp_at(device.secret, candidate), code):
            device.last_used_step = candidate
            device.save(update_fields=["last_used_step"])
            return True
    return False


def _match_backup(user: User, code: str) -> bool:
    normalized = code.replace("-", "").replace(" ", "").lower()
    if not normalized:
        return False
    backup = MfaBackupCode.objects.filter(
        user=user, code_hash=_hash(normalized), used_at__isnull=True
    ).first()
    if backup is None:
        return False
    backup.used_at = timezone.now()
    backup.save(update_fields=["used_at"])
    return True


def check_code(user: User, code: str, *, allow_backup: bool = True) -> bool:
    device = MfaDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
    if device is None:
        return False
    code = (code or "").strip()
    if _match_totp(device, code):
        return True
    return allow_backup and _match_backup(user, code)


# --- Enrollment -----------------------------------------------------------------


def start_setup(user: User) -> dict[str, str]:
    if mfa_enabled(user):
        raise Conflict("Two-factor authentication is already enabled.")
    secret = new_secret()
    MfaDevice.objects.update_or_create(
        user=user, defaults={"secret": secret, "confirmed_at": None, "last_used_step": None}
    )
    return {"secret": secret, "otpauth_uri": otpauth_uri(secret, user.email)}


def confirm_setup(user: User, code: str) -> list[str]:
    device = MfaDevice.objects.filter(user=user).first()
    if device is None or device.confirmed_at is not None:
        raise Conflict("Start the setup first.")
    if not _match_totp(device, (code or "").strip()):
        raise ValidationError({"code": ["The code is not valid."]})
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(BACKUP_CODE_COUNT)]
    with transaction.atomic():
        device.confirmed_at = timezone.now()
        device.save(update_fields=["confirmed_at"])
        MfaBackupCode.objects.filter(user=user).delete()
        MfaBackupCode.objects.bulk_create(
            [MfaBackupCode(user=user, code_hash=_hash(code.replace("-", ""))) for code in codes]
        )
    return codes


class MfaMandatory(ApplicationError):
    status_code = 403
    default_detail = "Two-factor authentication is mandatory for your role."
    default_code = "permission_denied"


def disable(user: User, *, password: str, code: str) -> None:
    if mfa_required(user):
        raise MfaMandatory()
    if not user.check_password(password or ""):
        raise ValidationError({"password": ["Incorrect password."]})
    if not check_code(user, code):
        raise ValidationError({"code": ["The code is not valid."]})
    MfaDevice.objects.filter(user=user).delete()
    MfaBackupCode.objects.filter(user=user).delete()


# --- Login challenge ----------------------------------------------------------------


def create_challenge(user: User) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    MfaChallenge.objects.create(
        user=user,
        token_hash=_hash(token),
        expires_at=timezone.now() + timedelta(seconds=settings.MFA_TOKEN_TTL_SECONDS),
    )
    return {"mfa_required": True, "mfa_token": token, "methods": ["TOTP"]}


def verify_challenge(token: str, code: str) -> User:
    with transaction.atomic():
        challenge = (
            MfaChallenge.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=_hash(token or ""))
            .first()
        )
        if (
            challenge is None
            or challenge.used_at is not None
            or challenge.expires_at <= timezone.now()
            or challenge.failed_attempts >= settings.MFA_MAX_ATTEMPTS
            or not challenge.user.is_active
        ):
            raise TokenInvalid()
        if not check_code(challenge.user, code):
            challenge.failed_attempts += 1
            challenge.save(update_fields=["failed_attempts"])
            invalid = ValidationError({"code": ["The code is not valid."]})
        else:
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["used_at"])
            return challenge.user
    raise invalid


__all__ = [
    "check_code",
    "confirm_setup",
    "create_challenge",
    "current_step",
    "disable",
    "mfa_enabled",
    "mfa_required",
    "new_secret",
    "otpauth_uri",
    "start_setup",
    "totp_at",
    "verify_challenge",
]
