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


def record_audit(
    *,
    actor: Any,
    action: str,
    object_type: str,
    object_id: Any,
    request: Any = None,
    metadata: dict[str, Any] | None = None,
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
        object_type=object_type,
        object_id=str(object_id),
        request_id=request_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata=metadata or {},
    )
