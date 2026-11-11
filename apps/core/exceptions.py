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

    def __init__(self, detail: Any = None, *, details: Any = None) -> None:
        super().__init__(detail)
        self.details_payload = details


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


class PaymentFailed(ApplicationError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = "The payment was declined. Try again with a new token."
    default_code = "payment_failed"


class AssessmentLocked(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This assessment is no longer editable."
    default_code = "assessment_locked"


class AssessmentIncomplete(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The assessment is not complete."
    default_code = "assessment_incomplete"


class UploadLimitReached(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The maximum number of lab files has been reached."
    default_code = "upload_limit_reached"


class NoLabFiles(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Upload at least one lab file first."
    default_code = "no_lab_files"


class TargetWeightInvalid(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The target weight must be lower than the current weight."
    default_code = "target_weight_invalid"


class TargetBelowHealthyRange(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "The target weight is below a healthy BMI range."
    default_code = "target_below_healthy_range"


class UnderAge(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Patients must be 18 or older."
    default_code = "under_age"


class ReadOnlyField(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "This field is read-only."
    default_code = "read_only_field"

    def __init__(self, field: str) -> None:
        super().__init__(self.default_detail, details={field: ["This field is read-only."]})


class UnsupportedDeliveryArea(ApplicationError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Delivery is not available for this address."
    default_code = "unsupported_delivery_area"


class EvaluationRequired(ApplicationError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "A paid or waived evaluation is required."
    default_code = "evaluation_required"


class NotInCareTeam(ApplicationError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "This person is not part of your care team."
    default_code = "not_in_care_team"


class AddressInUse(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This address is used by an open order or an active subscription."
    default_code = "address_in_use"


class SlotUnavailable(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This time slot is no longer available."
    default_code = "slot_unavailable"


class TooLateToChange(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "It is too late to change this consultation."
    default_code = "too_late_to_change"


class JoinWindowClosed(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The video call is not open right now."
    default_code = "join_window_closed"


class NoRefillsRemaining(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "No refills remain. Request a renewal from your physician."
    default_code = "no_refills_remaining"


class CancelNotAllowed(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This order can no longer be cancelled. Contact support."
    default_code = "cancel_not_allowed"


class PaymentMethodInUse(ApplicationError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "This card is the only payment method of an active subscription."
    default_code = "payment_method_in_use"


class PayloadTooLarge(ApplicationError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_detail = "The request body is too large."
    default_code = "payload_too_large"


class CredentialsNotVerified(ApplicationError):
    status_code = 403
    default_detail = "Your medical credentials have not been verified yet."
    default_code = "credentials_not_verified"


class MfaEnrollmentRequired(ApplicationError):
    status_code = 403
    default_detail = "Set up two-factor authentication to continue."
    default_code = "mfa_enrollment_required"


class ClaimRequired(ApplicationError):
    status_code = 403
    default_detail = "Claim this evaluation before opening it."
    default_code = "claim_required"


class AlreadyClaimed(ApplicationError):
    status_code = 409
    default_detail = "Another physician is already reviewing this evaluation."
    default_code = "already_claimed"


class OverlappingRule(ApplicationError):
    status_code = 409
    default_detail = "This availability overlaps another rule on the same day."
    default_code = "overlapping_rule"


class ConflictsWithConsultations(ApplicationError):
    status_code = 409
    default_detail = "This change conflicts with booked consultations."
    default_code = "conflicts_with_consultations"


class PreconditionFailed(ApplicationError):
    status_code = 412
    default_detail = "The record changed since you loaded it. Reload and try again."
    default_code = "precondition_failed"


class PreconditionRequired(ApplicationError):
    status_code = 428
    default_detail = "The If-Match header is required."
    default_code = "precondition_required"


class FlagsNotAcknowledged(ApplicationError):
    status_code = 400
    default_detail = "Acknowledge every flagged answer before approving."
    default_code = "flags_not_acknowledged"


class DoseOutOfRange(ApplicationError):
    status_code = 400
    default_detail = "This dose is not allowed for the product."
    default_code = "dose_out_of_range"


class InvitationExpired(ApplicationError):
    status_code = 410
    default_detail = "This invitation has expired."
    default_code = "invitation_expired"


class InvitationRevoked(ApplicationError):
    status_code = 410
    default_detail = "This invitation was revoked."
    default_code = "invitation_revoked"


class InvitationAlreadyUsed(ApplicationError):
    status_code = 410
    default_detail = "This invitation was already used."
    default_code = "invitation_already_used"


_STATUS_CODES = {
    status.HTTP_400_BAD_REQUEST: "validation_error",
    status.HTTP_401_UNAUTHORIZED: "not_authenticated",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_406_NOT_ACCEPTABLE: "not_acceptable",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_410_GONE: "gone",
    status.HTTP_412_PRECONDITION_FAILED: "precondition_failed",
    status.HTTP_428_PRECONDITION_REQUIRED: "precondition_required",
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
    "payment_failed": "The payment was declined. Try again with a new token.",
    "assessment_locked": "This assessment is no longer editable.",
    "assessment_incomplete": "The assessment is not complete.",
    "upload_limit_reached": "The maximum number of lab files has been reached.",
    "no_lab_files": "Upload at least one lab file first.",
    "target_weight_invalid": "The target weight must be lower than the current weight.",
    "target_below_healthy_range": "The target weight is below a healthy BMI range.",
    "under_age": "Patients must be 18 or older.",
    "read_only_field": "This field is read-only.",
    "unsupported_delivery_area": "Delivery is not available for this address.",
    "evaluation_required": "A paid or waived evaluation is required.",
    "not_in_care_team": "This person is not part of your care team.",
    "address_in_use": "This address is used by an open order or an active subscription.",
    "slot_unavailable": "This time slot is no longer available.",
    "too_late_to_change": "It is too late to change this consultation.",
    "join_window_closed": "The video call is not open right now.",
    "no_refills_remaining": "No refills remain. Request a renewal from your physician.",
    "cancel_not_allowed": "This order can no longer be cancelled. Contact support.",
    "payment_method_in_use": "This card is the only payment method of an active subscription.",
    "payload_too_large": "The request body is too large.",
    "credentials_not_verified": "Your medical credentials have not been verified yet.",
    "mfa_enrollment_required": "Set up two-factor authentication to continue.",
    "claim_required": "Claim this evaluation before opening it.",
    "already_claimed": "Another physician is already reviewing this evaluation.",
    "overlapping_rule": "This availability overlaps another rule on the same day.",
    "conflicts_with_consultations": "This change conflicts with booked consultations.",
    "precondition_failed": "The record changed since you loaded it. Reload and try again.",
    "precondition_required": "The If-Match header is required.",
    "flags_not_acknowledged": "Acknowledge every flagged answer before approving.",
    "dose_out_of_range": "This dose is not allowed for the product.",
    "invitation_expired": "This invitation has expired.",
    "invitation_revoked": "This invitation was revoked.",
    "invitation_already_used": "This invitation was already used.",
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
    "payment_failed": "El pago fue rechazado. Inténtalo de nuevo con un token nuevo.",
    "assessment_locked": "Esta evaluación ya no se puede editar.",
    "assessment_incomplete": "La evaluación no está completa.",
    "upload_limit_reached": "Se alcanzó el número máximo de archivos de laboratorio.",
    "no_lab_files": "Sube al menos un archivo de laboratorio primero.",
    "target_weight_invalid": "El peso objetivo debe ser menor que el peso actual.",
    "target_below_healthy_range": "El peso objetivo está por debajo de un rango de IMC saludable.",
    "under_age": "Los pacientes deben tener 18 años o más.",
    "read_only_field": "Este campo es de solo lectura.",
    "unsupported_delivery_area": "No hay entregas disponibles para esta dirección.",
    "evaluation_required": "Se requiere una evaluación pagada o exonerada.",
    "not_in_care_team": "Esta persona no forma parte de tu equipo de atención.",
    "address_in_use": "Esta dirección está en uso por un pedido abierto o una suscripción activa.",
    "slot_unavailable": "Este horario ya no está disponible.",
    "too_late_to_change": "Ya es demasiado tarde para cambiar esta consulta.",
    "join_window_closed": "La videollamada no está abierta en este momento.",
    "no_refills_remaining": "No quedan recargas. Solicita una renovación a tu médico.",
    "cancel_not_allowed": "Este pedido ya no se puede cancelar. Contacta a soporte.",
    "payment_method_in_use": "Esta tarjeta es el único método de pago de una suscripción activa.",
    "payload_too_large": "El cuerpo de la solicitud es demasiado grande.",
    "credentials_not_verified": "Tus credenciales médicas aún no han sido verificadas.",
    "mfa_enrollment_required": "Configura la autenticación de dos factores para continuar.",
    "claim_required": "Toma esta evaluación antes de abrirla.",
    "already_claimed": "Otro médico ya está revisando esta evaluación.",
    "overlapping_rule": "Esta disponibilidad se superpone con otra regla del mismo día.",
    "conflicts_with_consultations": "Este cambio entra en conflicto con consultas reservadas.",
    "precondition_failed": "El registro cambió. Recarga e inténtalo de nuevo.",
    "precondition_required": "El encabezado If-Match es obligatorio.",
    "flags_not_acknowledged": "Confirma todas las respuestas marcadas antes de aprobar.",
    "dose_out_of_range": "Esta dosis no está permitida para el producto.",
    "invitation_expired": "Esta invitación ha expirado.",
    "invitation_revoked": "Esta invitación fue revocada.",
    "invitation_already_used": "Esta invitación ya fue utilizada.",
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
    if isinstance(exc, ApplicationError) and exc.details_payload is not None:
        return exc.details_payload
    if code == "unknown_field" and isinstance(exc, UnknownField):
        return {exc.field: ["Unknown field."]}
    if code == "validation_error":
        return data
    if isinstance(data, dict) and set(data) == {"detail"}:
        return {}
    return data if isinstance(data, (dict, list)) else {}
