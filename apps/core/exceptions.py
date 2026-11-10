"""Uniform API error envelope and domain error types (contract Part 1)."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.utils.translation import get_language
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


class InvalidStateTransition(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The requested state transition is not allowed."
    default_code = "invalid_state_transition"


class IdempotencyConflict(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The idempotency key was already used with a different request."
    default_code = "idempotency_conflict"


class AccountLocked(ApplicationError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = "Too many failed attempts. Try again later."
    default_code = "account_locked"
    retry_after = 900


class ServiceUnavailable(ApplicationError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The service is temporarily unavailable."
    default_code = "service_unavailable"


class InvalidCredentials(ApplicationError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid email or password."
    default_code = "invalid_credentials"


class AccountDisabled(ApplicationError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This account is disabled."
    default_code = "account_disabled"


class TokenInvalid(ApplicationError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "The token is invalid."
    default_code = "token_invalid"


class TokenExpired(ApplicationError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "The token has expired."
    default_code = "token_expired"


class TokenInvalidInput(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The token is invalid."
    default_code = "token_invalid"


class TokenExpiredInput(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The token has expired."
    default_code = "token_expired"


class EmailNotVerified(ApplicationError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Verify your email address to continue."
    default_code = "email_not_verified"


class ConsentRequired(ApplicationError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Current Terms of Service and Privacy Policy consent is required."
    default_code = "consent_required"


class ConsentRequiredInput(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Current Terms of Service and Privacy Policy consent is required."
    default_code = "consent_required"


class IdempotencyKeyRequired(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The Idempotency-Key header is required."
    default_code = "idempotency_key_required"


class UnknownField(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Unknown field."
    default_code = "unknown_field"

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(self.default_detail)


class FileTooLarge(ApplicationError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "The uploaded file is too large."
    default_code = "file_too_large"


_STATUS_CODES = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "not_authenticated",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_406_NOT_ACCEPTABLE: "not_acceptable",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "file_too_large",
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
    status.HTTP_429_TOO_MANY_REQUESTS: "throttled",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
}

_DEFAULT_MESSAGES = {
    "validation_error": "Validation failed.",
    "unknown_field": "Unknown field.",
    "idempotency_key_required": "The Idempotency-Key header is required.",
    "not_authenticated": "Authentication credentials were not provided.",
    "invalid_credentials": "Invalid email or password.",
    "token_invalid": "The token is invalid.",
    "token_expired": "The token has expired.",
    "account_disabled": "This account is disabled.",
    "account_locked": "Too many failed attempts. Try again later.",
    "email_not_verified": "Verify your email address to continue.",
    "consent_required": "Current Terms of Service and Privacy Policy consent is required.",
    "permission_denied": "You do not have permission to perform this action.",
    "not_found": "Not found.",
    "method_not_allowed": "Method not allowed.",
    "not_acceptable": "Could not satisfy the request Accept header.",
    "conflict": "The resource is in a conflicting state.",
    "invalid_state_transition": "The requested state transition is not allowed.",
    "idempotency_conflict": "The idempotency key was already used with a different request.",
    "file_too_large": "The uploaded file is too large.",
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
    response.data = {
        "code": code,
        "message": _message(exc, code),
        "details": _details(exc, response.data, code),
        "request_id": request_id,
    }
    if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        retry_after = getattr(exc, "retry_after", None) or getattr(exc, "wait", None) or 60
        response["Retry-After"] = str(max(1, int(retry_after)))
    return response


def _error_code(exc: Exception, response: Response) -> str:
    if isinstance(exc, ApplicationError):
        return str(exc.default_code)
    if isinstance(exc, exceptions.NotAuthenticated):
        return "not_authenticated"
    if isinstance(exc, exceptions.AuthenticationFailed):
        return "invalid_credentials"
    if isinstance(exc, exceptions.PermissionDenied):
        return "permission_denied"
    if isinstance(exc, exceptions.NotFound):
        return "not_found"
    if isinstance(exc, exceptions.ValidationError):
        return "validation_error"
    if isinstance(exc, exceptions.Throttled):
        return "throttled"
    if isinstance(exc, exceptions.UnsupportedMediaType):
        return "unsupported_media_type"
    if isinstance(exc, exceptions.MethodNotAllowed):
        return "method_not_allowed"
    if isinstance(exc, exceptions.NotAcceptable):
        return "not_acceptable"
    if isinstance(exc, exceptions.ParseError):
        return "validation_error"
    return _STATUS_CODES.get(response.status_code, "error")


_MESSAGES_ES = {
    "validation_error": "La validación falló.",
    "unknown_field": "Campo desconocido.",
    "idempotency_key_required": "El encabezado Idempotency-Key es obligatorio.",
    "not_authenticated": "No se proporcionaron credenciales de autenticación.",
    "invalid_credentials": "Correo o contraseña inválidos.",
    "token_invalid": "El token es inválido.",
    "token_expired": "El token ha expirado.",
    "account_disabled": "Esta cuenta está deshabilitada.",
    "account_locked": "Demasiados intentos fallidos. Inténtalo más tarde.",
    "email_not_verified": "Verifica tu correo para continuar.",
    "consent_required": "Debes aceptar los Términos y la Política de Privacidad vigentes.",
    "permission_denied": "No tienes permiso para realizar esta acción.",
    "not_found": "No encontrado.",
    "method_not_allowed": "Método no permitido.",
    "not_acceptable": "No se puede satisfacer el encabezado Accept.",
    "conflict": "El recurso está en un estado conflictivo.",
    "invalid_state_transition": "La transición de estado no está permitida.",
    "idempotency_conflict": "La clave de idempotencia ya se usó con otra solicitud.",
    "file_too_large": "El archivo es demasiado grande.",
    "unsupported_media_type": "Tipo de contenido no soportado.",
    "throttled": "La solicitud fue limitada.",
    "service_unavailable": "El servicio no está disponible temporalmente.",
    "server_error": "Ocurrió un error inesperado.",
}


def _message(exc: Exception, code: str) -> str:
    if isinstance(exc, exceptions.ValidationError):
        message = _DEFAULT_MESSAGES["validation_error"]
    elif isinstance(exc, exceptions.APIException):
        detail = exc.detail
        message = (
            detail
            if isinstance(detail, str)
            else _DEFAULT_MESSAGES.get(code, "The request could not be processed.")
        )
    else:
        message = _DEFAULT_MESSAGES.get(code, "The request could not be processed.")
    if (get_language() or "").lower().startswith("es"):
        return _MESSAGES_ES.get(code, message)
    return message


def _details(exc: Exception, data: Any, code: str) -> Any:
    if code == "unknown_field" and isinstance(exc, UnknownField):
        return {exc.field: ["Unknown field."]}
    if code == "validation_error":
        return data
    if isinstance(data, dict) and set(data) == {"detail"}:
        return {}
    return data if isinstance(data, (dict, list)) else {}
