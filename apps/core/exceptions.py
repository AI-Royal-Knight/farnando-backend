"""Uniform API error envelope and domain error types."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.middleware import get_request_id


class ApplicationError(exceptions.APIException):
    """Base class for errors that carry a stable machine-readable code."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    default_detail = "The request could not be processed."
    default_code = "application_error"


class Conflict(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The resource is in a conflicting state."
    default_code = "conflict"


class AccountLocked(ApplicationError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many failed attempts. Try again later."
    default_code = "account_locked"


class ServiceUnavailable(ApplicationError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The service is temporarily unavailable."
    default_code = "service_unavailable"


_STATUS_CODES = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "authentication_required",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_406_NOT_ACCEPTABLE: "not_acceptable",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "payload_too_large",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
    status.HTTP_429_TOO_MANY_REQUESTS: "throttled",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
}

_DEFAULT_MESSAGES = {
    "validation_error": "Validation failed.",
    "authentication_required": "Authentication credentials were not provided or are invalid.",
    "permission_denied": "You do not have permission to perform this action.",
    "not_found": "Not found.",
    "method_not_allowed": "Method not allowed.",
    "not_acceptable": "Could not satisfy the request Accept header.",
    "conflict": "The resource is in a conflicting state.",
    "payload_too_large": "The uploaded payload is too large.",
    "unsupported_media_type": "Unsupported media type.",
    "throttled": "Request was throttled.",
    "service_unavailable": "The service is temporarily unavailable.",
    "server_error": "An unexpected error occurred.",
}


def exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Render DRF and Django exceptions using the public error envelope."""

    request = context.get("request")
    request_id = get_request_id(request)

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled exceptions are left to Django/Sentry; never echo internals.
        return None

    code = _error_code(exc, response)
    message = _message(exc, code)
    details = _details(response.data, code)

    response.data = {
        "code": code,
        "message": message,
        "details": details,
        "request_id": request_id,
    }
    return response


def _error_code(exc: Exception, response: Response) -> str:
    if isinstance(exc, exceptions.APIException):
        code = exc.get_codes()
        if isinstance(code, str):
            return code
    return _STATUS_CODES.get(response.status_code, "error")


def _message(exc: Exception, code: str) -> str:
    if isinstance(exc, exceptions.ValidationError):
        return _DEFAULT_MESSAGES["validation_error"]
    if isinstance(exc, exceptions.APIException):
        detail = exc.detail
        if isinstance(detail, str):
            return detail
    return _DEFAULT_MESSAGES.get(code, "The request could not be processed.")


def _details(data: Any, code: str) -> Any:
    if code == "validation_error":
        return data
    if isinstance(data, dict) and set(data) == {"detail"}:
        return {}
    return data if isinstance(data, (dict, list)) else {}
