"""Contract-shaped response for django-axes lockouts.

The default axes response is plain text and is not the API error envelope. The
contract requires ``429`` with ``code: account_locked``, a localized ``message``
and a ``Retry-After`` header (Part 1, section 1.2).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.translation import get_language

from apps.core.middleware import get_request_id

_MESSAGES = {
    "en": "Too many failed attempts. Try again later.",
    "es": "Demasiados intentos fallidos. Inténtalo más tarde.",
}


def _retry_after_seconds() -> int:
    cool_off = getattr(settings, "AXES_COOLOFF_TIME", None)
    if isinstance(cool_off, timedelta):
        return max(1, int(cool_off.total_seconds()))
    return 15 * 60


def axes_lockout_response(
    request: HttpRequest,
    original_response: HttpResponse | None = None,
    credentials: dict[str, Any] | None = None,
) -> HttpResponse:
    """Return the standard error envelope instead of axes' plain-text page."""

    _ = original_response, credentials
    language = (get_language() or "en").lower()
    message = _MESSAGES["es"] if language.startswith("es") else _MESSAGES["en"]
    response = JsonResponse(
        {
            "code": "account_locked",
            "message": message,
            "details": {},
            "request_id": get_request_id(request),
        },
        status=429,
    )
    response["Retry-After"] = str(_retry_after_seconds())
    return response


__all__ = ["axes_lockout_response"]
