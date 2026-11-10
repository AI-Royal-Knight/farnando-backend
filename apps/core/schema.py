"""Shared drf-spectacular response declarations."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse

from apps.core.serializers import ErrorEnvelopeSerializer


def error_response(description: str) -> OpenApiResponse:
    return OpenApiResponse(response=ErrorEnvelopeSerializer, description=description)


COMMON_ERRORS = {
    400: error_response("Validation error."),
    401: error_response("Authentication required or token invalid."),
    403: error_response("Permission denied."),
    404: error_response("Object not found in the caller's scope."),
    409: error_response("Conflicting state or idempotency key reuse."),
    429: error_response("Rate limit or brute-force lockout."),
}
