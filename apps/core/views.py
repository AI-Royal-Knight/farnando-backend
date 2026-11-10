"""Liveness and readiness probes.

The responses deliberately expose only component names and booleans; exception
messages can contain hostnames or credentials and must never leave the server.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from django.views.defaults import page_not_found
from django.views.defaults import server_error as default_server_error

from apps.core.middleware import get_request_id
from celery import current_app as celery_app

logger = structlog.get_logger(__name__)

_CHECK_TIMEOUT_SECONDS = 3


def _check_database() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        return bool(cursor.fetchone() == (1,))


def _check_cache() -> bool:
    key = "readyz:probe"
    cache.set(key, "ok", timeout=5)
    result = cache.get(key) == "ok"
    cache.delete(key)
    return bool(result)


def _check_broker() -> bool:
    with celery_app.connection_for_read() as conn:
        conn.ensure_connection(max_retries=1, timeout=_CHECK_TIMEOUT_SECONDS)
    return True


def _check_storage() -> bool:
    default_storage.exists("healthchecks/readyz-probe")
    return True


@require_GET
def liveness(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def readiness(request: HttpRequest) -> JsonResponse:
    checks: dict[str, bool] = {}
    probes: dict[str, Callable[[], bool]] = {
        "database": _check_database,
        "cache": _check_cache,
        "broker": _check_broker,
        "storage": _check_storage,
    }
    for name, probe in probes.items():
        try:
            checks[name] = probe()
        except Exception as exc:
            checks[name] = False
            logger.warning("readiness_check_failed", component=name, error=type(exc).__name__)
    healthy = all(checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status=200 if healthy else 503,
    )


def _is_api(request: HttpRequest) -> bool:
    return request.path_info.startswith("/api/")


def not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    if _is_api(request):
        return JsonResponse(
            {
                "code": "not_found",
                "message": "Not found.",
                "details": {},
                "request_id": get_request_id(request),
            },
            status=404,
        )
    return page_not_found(request, exception)


def server_error(request: HttpRequest) -> HttpResponse:
    if _is_api(request):
        return JsonResponse(
            {
                "code": "server_error",
                "message": "An unexpected error occurred.",
                "details": {},
                "request_id": get_request_id(request),
            },
            status=500,
        )
    return default_server_error(request)
