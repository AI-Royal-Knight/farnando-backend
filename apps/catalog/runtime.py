"""Runtime settings groups (contract Part 5, 9.5).

Admins edit the groups through ``/admin/settings/{group}/``. Reads fall back to
Django settings so tests and fresh databases behave like before Part 5.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache

_CACHE_KEY = "catalog:platform-settings:v1"
_CACHE_SECONDS = 60

DB_GROUPS = ("evaluation", "booking", "complaints", "support", "payments")


def defaults() -> dict[str, dict[str, Any]]:
    return {
        "evaluation": {
            "estimated_review_hours": {"min": 24, "max": 48},
            "claim_timeout_hours": settings.EVALUATION_CLAIM_TIMEOUT_HOURS,
            "urgent_after_hours": settings.EVALUATION_URGENT_AFTER_HOURS,
            "reapply_cooldown_days": 30,
        },
        "booking": {
            "min_booking_lead_hours": settings.CONSULTATION_MIN_BOOKING_LEAD_HOURS,
            "reschedule_min_hours_before": settings.CONSULTATION_RESCHEDULE_MIN_HOURS,
            "cancel_min_hours_before": settings.CONSULTATION_CANCEL_MIN_HOURS,
            "join_window_before_minutes": settings.CONSULTATION_JOIN_EARLY_MINUTES,
            "join_window_after_minutes": settings.CONSULTATION_JOIN_GRACE_MINUTES,
        },
        "complaints": {"response_sla_hours": settings.COMPLAINT_RESPONSE_SLA_HOURS},
        "support": {
            "first_response_sla_minutes": settings.SUPPORT_FIRST_RESPONSE_SLA_MINUTES,
            "auto_close_resolved_days": settings.SUPPORT_AUTO_CLOSE_RESOLVED_DAYS,
        },
        "payments": {
            "refund_approval_limit": {
                "amount": str(settings.REFUND_APPROVAL_LIMIT),
                "currency": settings.REPORTING_CURRENCY,
            },
            "refund_fee_on_not_suitable": settings.REFUND_FEE_ON_NOT_SUITABLE,
        },
    }


def _stored() -> dict[str, dict[str, Any]]:
    cached = cache.get(_CACHE_KEY)
    if isinstance(cached, dict):
        return cached
    from apps.catalog.models import PlatformSettings

    instance = PlatformSettings.objects.first()
    data: dict[str, dict[str, Any]] = {}
    if instance is not None:
        for group in DB_GROUPS:
            value = getattr(instance, group)
            data[group] = dict(value) if isinstance(value, dict) else {}
    cache.set(_CACHE_KEY, data, _CACHE_SECONDS)
    return data


def invalidate() -> None:
    cache.delete(_CACHE_KEY)


def group(name: str) -> dict[str, Any]:
    merged = dict(defaults()[name])
    merged.update(_stored().get(name, {}))
    return merged


def value(group_name: str, key: str) -> Any:
    return group(group_name)[key]


def hours(group_name: str, key: str) -> int:
    return int(value(group_name, key))


def refund_approval_limit() -> tuple[Decimal, str]:
    limit = value("payments", "refund_approval_limit")
    return Decimal(str(limit["amount"])), str(limit["currency"])


__all__ = ["defaults", "group", "hours", "invalidate", "refund_approval_limit", "value"]
