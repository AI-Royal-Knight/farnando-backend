"""Minimal append-only audit helper for clinical access."""

from __future__ import annotations

import ipaddress
from typing import Any

from django.http import HttpRequest

from apps.core.middleware import get_request_id
from apps.core.models import AuditLog


def _client_ip(request: HttpRequest) -> str | None:
    raw = request.META.get("REMOTE_ADDR")
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


_READ_SUFFIXES = ("viewed", "read", "listed", "packet_read", "chart_read")
_SUFFIX_TYPES = {
    "created": AuditLog.ActionType.CREATE,
    "submitted": AuditLog.ActionType.CREATE,
    "invited": AuditLog.ActionType.CREATE,
    "account_created": AuditLog.ActionType.CREATE,
    "deleted": AuditLog.ActionType.DELETE,
    "revoked": AuditLog.ActionType.DELETE,
    "downloaded": AuditLog.ActionType.DOWNLOAD,
    "download": AuditLog.ActionType.DOWNLOAD,
    "exported": AuditLog.ActionType.EXPORT,
    "export": AuditLog.ActionType.EXPORT,
    "permissions_changed": AuditLog.ActionType.PERMISSION_CHANGE,
    "login": AuditLog.ActionType.LOGIN,
}


def action_type_for(action: str) -> str:
    """Map a dotted event name (``patient.viewed``) to the contract enum."""

    suffix = action.rsplit(".", 1)[-1]
    if suffix in _READ_SUFFIXES or suffix.endswith("_viewed") or suffix.endswith("_read"):
        return AuditLog.ActionType.READ
    if suffix in _SUFFIX_TYPES:
        return _SUFFIX_TYPES[suffix]
    if suffix.endswith("_downloaded"):
        return AuditLog.ActionType.DOWNLOAD
    if suffix.endswith("_created"):
        return AuditLog.ActionType.CREATE
    if suffix.endswith("_deleted"):
        return AuditLog.ActionType.DELETE
    return AuditLog.ActionType.UPDATE


def record_audit(
    *,
    actor: Any,
    action: str,
    object_type: str,
    object_id: Any,
    request: Any = None,
    metadata: dict[str, Any] | None = None,
    patient_id: Any = None,
    changed_fields: list[str] | None = None,
    action_type: str | None = None,
) -> AuditLog:
    django_request: HttpRequest | None = getattr(request, "_request", request)
    user_agent = ""
    ip_address = None
    request_id = ""
    if django_request is not None:
        user_agent = str(django_request.META.get("HTTP_USER_AGENT", ""))[:1024]
        ip_address = _client_ip(django_request)
        request_id = get_request_id(django_request) or ""
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        action_type=action_type or action_type_for(action),
        patient_id=patient_id,
        changed_fields=list(changed_fields or []),
        object_type=object_type,
        object_id=str(object_id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata=metadata or {},
    )
