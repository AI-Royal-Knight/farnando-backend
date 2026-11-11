"""Create in-app notifications and queue preference-aware emails.

Every title and body here is generic on purpose: no diagnoses, medication
names, lab values or message text ever leave the app through a notification.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction

from apps.accounts.enums import Role
from apps.notifications.models import Notification, NotificationType

_COPY: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        NotificationType.EVALUATION_STATUS_CHANGED: (
            "Your evaluation has been updated",
            "Open the app to see the latest status.",
        ),
        NotificationType.LABS_REQUESTED: (
            "Your physician needs more information",
            "Open the app to see what is needed.",
        ),
        NotificationType.CONSULTATION_CONFIRMED: (
            "Your consultation is confirmed",
            "Open the app to see the details.",
        ),
        NotificationType.CONSULTATION_DECLINED: (
            "Your consultation request was not accepted",
            "Open the app to choose another time.",
        ),
        NotificationType.CONSULTATION_REMINDER: (
            "You have an upcoming consultation",
            "Open the app to see the time and join link.",
        ),
        NotificationType.CONSULTATION_CANCELLED: (
            "A consultation was cancelled",
            "Open the app to see the details.",
        ),
        NotificationType.NEW_MESSAGE: (
            "You have a new message",
            "Open the app to read it.",
        ),
        NotificationType.ORDER_STATUS_CHANGED: (
            "Your order has been updated",
            "Open the app to see the latest status.",
        ),
        NotificationType.DOSE_REMINDER: (
            "Reminder from your care plan",
            "Open the app to see your schedule.",
        ),
        NotificationType.REFILL_LOW: (
            "Time to plan your next refill",
            "Open the app to see your options.",
        ),
        NotificationType.SUBSCRIPTION_RENEWED: (
            "Your subscription has renewed",
            "Open the app to see the details.",
        ),
        NotificationType.SUBSCRIPTION_PAYMENT_FAILED: (
            "We could not process a payment",
            "Open the app to update your payment method.",
        ),
        NotificationType.SUPPORT_TICKET_REPLY: (
            "Support replied to your request",
            "Open the app to read the reply.",
        ),
        NotificationType.TREATMENT_UPDATED: (
            "Your care plan was updated",
            "Open the app to see the details.",
        ),
        NotificationType.NEW_EVALUATION_IN_QUEUE: (
            "A new evaluation is waiting for review",
            "Open the review queue.",
        ),
        NotificationType.EVALUATION_LABS_UPLOADED: (
            "A patient uploaded lab results",
            "Open the evaluation to continue the review.",
        ),
        NotificationType.CONSULTATION_REQUESTED: (
            "New consultation request",
            "Open your consultations to accept or decline it.",
        ),
        NotificationType.CONSULTATION_CANCELLED_BY_PATIENT: (
            "A patient cancelled a consultation",
            "Open your consultations to see the details.",
        ),
        NotificationType.CONSULTATION_RESCHEDULED_BY_PATIENT: (
            "A patient rescheduled a consultation",
            "Open your consultations to accept the new time.",
        ),
        NotificationType.RENEWAL_REQUESTED: (
            "A patient requested a prescription renewal",
            "Open the renewal requests.",
        ),
        NotificationType.CREDENTIALS_VERIFIED: (
            "Your credentials were verified",
            "You can now review evaluations.",
        ),
        NotificationType.CREDENTIALS_REJECTED: (
            "Your credentials could not be verified",
            "Open your profile to see the details.",
        ),
    },
    "es": {
        NotificationType.EVALUATION_STATUS_CHANGED: (
            "Tu evaluación fue actualizada",
            "Abre la aplicación para ver el estado.",
        ),
        NotificationType.LABS_REQUESTED: (
            "Tu médico necesita más información",
            "Abre la aplicación para ver qué se necesita.",
        ),
        NotificationType.CONSULTATION_CONFIRMED: (
            "Tu consulta está confirmada",
            "Abre la aplicación para ver los detalles.",
        ),
        NotificationType.CONSULTATION_DECLINED: (
            "Tu solicitud de consulta no fue aceptada",
            "Abre la aplicación para elegir otro horario.",
        ),
        NotificationType.CONSULTATION_REMINDER: (
            "Tienes una consulta próxima",
            "Abre la aplicación para ver la hora y el enlace.",
        ),
        NotificationType.CONSULTATION_CANCELLED: (
            "Se canceló una consulta",
            "Abre la aplicación para ver los detalles.",
        ),
        NotificationType.NEW_MESSAGE: (
            "Tienes un mensaje nuevo",
            "Abre la aplicación para leerlo.",
        ),
        NotificationType.ORDER_STATUS_CHANGED: (
            "Tu pedido fue actualizado",
            "Abre la aplicación para ver el estado.",
        ),
        NotificationType.DOSE_REMINDER: (
            "Recordatorio de tu plan de cuidado",
            "Abre la aplicación para ver tu horario.",
        ),
        NotificationType.REFILL_LOW: (
            "Planifica tu próxima recarga",
            "Abre la aplicación para ver tus opciones.",
        ),
        NotificationType.SUBSCRIPTION_RENEWED: (
            "Tu suscripción se renovó",
            "Abre la aplicación para ver los detalles.",
        ),
        NotificationType.SUBSCRIPTION_PAYMENT_FAILED: (
            "No pudimos procesar un pago",
            "Abre la aplicación para actualizar tu método de pago.",
        ),
        NotificationType.SUPPORT_TICKET_REPLY: (
            "Soporte respondió tu solicitud",
            "Abre la aplicación para leer la respuesta.",
        ),
        NotificationType.TREATMENT_UPDATED: (
            "Tu plan de cuidado fue actualizado",
            "Abre la aplicación para ver los detalles.",
        ),
        NotificationType.NEW_EVALUATION_IN_QUEUE: (
            "Hay una nueva evaluación por revisar",
            "Abre la cola de revisión.",
        ),
        NotificationType.EVALUATION_LABS_UPLOADED: (
            "Un paciente subió resultados de laboratorio",
            "Abre la evaluación para continuar la revisión.",
        ),
        NotificationType.CONSULTATION_REQUESTED: (
            "Nueva solicitud de consulta",
            "Abre tus consultas para aceptarla o rechazarla.",
        ),
        NotificationType.CONSULTATION_CANCELLED_BY_PATIENT: (
            "Un paciente canceló una consulta",
            "Abre tus consultas para ver los detalles.",
        ),
        NotificationType.CONSULTATION_RESCHEDULED_BY_PATIENT: (
            "Un paciente reprogramó una consulta",
            "Abre tus consultas para aceptar el nuevo horario.",
        ),
        NotificationType.RENEWAL_REQUESTED: (
            "Un paciente solicitó renovar una receta",
            "Abre las solicitudes de renovación.",
        ),
        NotificationType.CREDENTIALS_VERIFIED: (
            "Tus credenciales fueron verificadas",
            "Ya puedes revisar evaluaciones.",
        ),
        NotificationType.CREDENTIALS_REJECTED: (
            "No pudimos verificar tus credenciales",
            "Abre tu perfil para ver los detalles.",
        ),
    },
}

# Preference field that gates the email for each type. ``None`` = always sent
# (care-critical, see docs/contract-deviations.md #71).
EMAIL_PREFERENCE: dict[str, str | None] = {
    NotificationType.EVALUATION_STATUS_CHANGED: None,
    NotificationType.LABS_REQUESTED: None,
    NotificationType.SUPPORT_TICKET_REPLY: None,
    NotificationType.NEW_MESSAGE: "email_messages",
    NotificationType.ORDER_STATUS_CHANGED: "email_orders",
    NotificationType.SUBSCRIPTION_RENEWED: "email_orders",
    NotificationType.SUBSCRIPTION_PAYMENT_FAILED: "email_orders",
    NotificationType.CONSULTATION_CONFIRMED: "email_reminders",
    NotificationType.CONSULTATION_DECLINED: "email_reminders",
    NotificationType.CONSULTATION_CANCELLED: "email_reminders",
    NotificationType.CONSULTATION_REMINDER: "email_reminders",
    NotificationType.DOSE_REMINDER: "email_reminders",
    NotificationType.REFILL_LOW: "email_reminders",
    NotificationType.TREATMENT_UPDATED: "email_reminders",
}


def _language(user: Any) -> str:
    profile = getattr(user, "patient_profile", None) if user.role == Role.PATIENT else None
    language = getattr(profile, "language", "es") if profile is not None else "es"
    return language if language in _COPY else "es"


def _wants_email(user: Any, notification_type: str) -> bool:
    if user.role != Role.PATIENT or not user.is_active:
        return False
    field = EMAIL_PREFERENCE.get(notification_type)
    if field is None:
        return True
    from apps.patients.services import get_or_create_profile

    return bool(getattr(get_or_create_profile(user), field))


def notify(
    user: Any,
    notification_type: str,
    *,
    target_kind: str = "",
    target_id: Any = None,
    email: bool = True,
) -> Notification:
    title, body = _COPY[_language(user)][notification_type]
    notification = Notification.objects.create(
        user=user,
        type=notification_type,
        title=title,
        body=body,
        target_kind=target_kind,
        target_id=target_id,
    )
    if email and _wants_email(user, notification_type):
        from apps.notifications.tasks import send_notification_email

        notification_id = str(notification.pk)
        transaction.on_commit(lambda: send_notification_email.delay(notification_id))
    return notification


def unread_count(user: Any) -> int:
    return Notification.objects.filter(user=user, is_read=False).count()


__all__ = ["EMAIL_PREFERENCE", "notify", "unread_count"]
