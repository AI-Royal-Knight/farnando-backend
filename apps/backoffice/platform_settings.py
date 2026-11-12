"""Settings groups (Part 5, 9.5). Validation lives in the serializers."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from rest_framework.exceptions import NotFound

from apps.accounts.models import User
from apps.catalog import runtime
from apps.catalog.models import PlatformSettings, PublicStats, SiteSettings
from apps.core.audit import record_audit

GROUPS = (
    "site",
    "stats",
    "evaluation",
    "booking",
    "complaints",
    "support",
    "messaging",
    "payments",
)
SITE_FIELDS = ("contact_email", "contact_phone", "address", "social_links", "legal_entity_name")
STATS_FIELDS = ("sustained_weight_loss_percent", "consultations_delivered", "average_reply_minutes")


def _check(group: str) -> None:
    if group not in GROUPS:
        raise NotFound()


def read(group: str) -> dict[str, Any]:
    _check(group)
    if group == "site":
        site = SiteSettings.load()
        return {field: getattr(site, field) for field in SITE_FIELDS}
    if group == "stats":
        stats = PublicStats.load()
        return {field: getattr(stats, field) for field in STATS_FIELDS}
    if group == "messaging":
        return {"safety_notice": SiteSettings.load().messaging_safety_notice}
    return runtime.group(group)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def write(
    *, group: str, data: dict[str, Any], reason: str, actor: User, request: Any
) -> dict[str, Any]:
    _check(group)
    old = _jsonable(read(group))
    new = _jsonable(data)
    with transaction.atomic():
        if group == "site":
            site = SiteSettings.load()
            for field in SITE_FIELDS:
                setattr(site, field, data[field])
            site.save()
        elif group == "stats":
            stats = PublicStats.load()
            for field in STATS_FIELDS:
                setattr(stats, field, data[field])
            stats.save()
        elif group == "messaging":
            site = SiteSettings.load()
            site.messaging_safety_notice = data["safety_notice"]
            site.save(update_fields=["messaging_safety_notice", "updated_at"])
        else:
            instance = PlatformSettings.load()
            setattr(instance, group, new)
            instance.save(update_fields=[group, "updated_at"])
            transaction.on_commit(runtime.invalidate)
        changed = sorted(key for key in new if old.get(key) != new.get(key))
        record_audit(
            actor=actor,
            action="settings.updated",
            object_type="Settings",
            object_id=group,
            request=request,
            changed_fields=changed,
            metadata={"group": group, "reason": reason, "old": old, "new": new},
        )
    runtime.invalidate()
    return read(group)
