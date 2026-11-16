"""Idempotent email tasks. Arguments contain only user ids, never PII."""

from __future__ import annotations

from urllib.parse import urlencode

import structlog
from django.conf import settings

from apps.accounts.models import User
from apps.accounts.tokens import make_email_verification_token, make_password_reset_token
from apps.core.email import get_email_service
from celery import shared_task

logger = structlog.get_logger(__name__)


class EmailDeliveryError(RuntimeError):
    """Sanitised transport error safe to place in Celery's traceback."""


_TASK_OPTIONS = {
    "bind": True,
    "autoretry_for": (EmailDeliveryError,),
    "retry_backoff": True,
    "retry_jitter": True,
    "max_retries": 5,
    "acks_late": True,
    "time_limit": 60,
    "soft_time_limit": 45,
}


def _send(*, to: str, subject: str, template: str, context: dict[str, object]) -> None:
    try:
        get_email_service().send(to=to, subject=subject, template=template, context=context)
    except Exception as exc:
        logger.warning("email_delivery_failed", template=template, error=type(exc).__name__)
        raise EmailDeliveryError("Transactional email delivery failed.") from None


def _verification_url(user: User) -> str:
    query = urlencode({"token": make_email_verification_token(user)})
    return f"{settings.FRONTEND_URL}/verify-email?{query}"


def _reset_url(user: User) -> str:
    query = urlencode({"token": make_password_reset_token(user)})
    return f"{settings.FRONTEND_URL}/reset-password?{query}"


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_verification_email(self: object, user_id: str) -> None:
    _ = self
    user = User.objects.filter(pk=user_id, is_active=True, is_email_verified=False).first()
    if user is None:
        return
    _send(
        to=user.email,
        subject="Confirma tu correo — Adelgaceya",
        template="email_verification",
        context={
            "first_name": user.get_short_name(),
            "full_name": user.full_name or user.get_short_name(),
            "verification_url": _verification_url(user),
            "expires_hours": settings.EMAIL_VERIFICATION_TIMEOUT // 3600,
        },
    )
    logger.info("verification_email_sent", user_id=str(user.pk))


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_password_reset_email(self: object, user_id: str) -> None:
    _ = self
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return
    _send(
        to=user.email,
        subject="Restablece tu contraseña — Adelgaceya",
        template="password_reset",
        context={
            "first_name": user.get_short_name(),
            "full_name": user.full_name or user.get_short_name(),
            "reset_url": _reset_url(user),
            "expires_hours": settings.PASSWORD_RESET_TIMEOUT // 3600,
        },
    )
    logger.info("password_reset_email_sent", user_id=str(user.pk))


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_account_exists_email(self: object, user_id: str) -> None:
    _ = self
    user = User.objects.filter(pk=user_id, is_active=True).first()
    if user is None:
        return
    _send(
        to=user.email,
        subject="Tu cuenta de Adelgaceya",
        template="account_exists",
        context={
            "first_name": user.get_short_name(),
            "full_name": user.full_name or user.get_short_name(),
            "reset_url": _reset_url(user),
            "expires_hours": settings.PASSWORD_RESET_TIMEOUT // 3600,
        },
    )
    logger.info("account_exists_email_sent", user_id=str(user.pk))
