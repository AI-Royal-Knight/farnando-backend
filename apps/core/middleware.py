"""Request correlation and admin-only CSP relaxation."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from django.http import HttpRequest, HttpResponse

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIDMiddleware:
    """Attach a validated request id to the request, logs and response.

    Incoming identifiers are only accepted when they match a conservative
    character set; anything else is replaced. The id is bound into structlog's
    contextvars so every log line emitted while handling the request carries
    it without callers having to pass it around.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = self.get_response(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response[REQUEST_ID_HEADER] = request_id
        return response


class AdminCSPMiddleware:
    """Allow Django admin's inline bootstrap scripts without weakening the API.

    Django admin ships inline ``<script>`` blocks with JSON constants. Swagger
    UI receives the same relaxation through a per-view decorator instead, so
    the global policy stays strict.
    """

    _RELAXED_PATHS = ("/admin/",)

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.path_info.startswith(self._RELAXED_PATHS):
            header = response.get("Content-Security-Policy")
            if header:
                response["Content-Security-Policy"] = _add_unsafe_inline(header)
        return response


def _add_unsafe_inline(policy: str) -> str:
    directives: list[str] = []
    for raw in policy.split(";"):
        directive = raw.strip()
        if not directive:
            continue
        name, _, value = directive.partition(" ")
        if (
            name in {"script-src", "script-src-elem", "style-src", "style-src-elem"}
            and "'unsafe-inline'" not in value
        ):
            directive = f"{name} {value} 'unsafe-inline'".strip()
        directives.append(directive)
    return "; ".join(directives)


def get_request_id(request: Any) -> str:
    value: Any = getattr(request, "request_id", None)
    return value if isinstance(value, str) else "-"
