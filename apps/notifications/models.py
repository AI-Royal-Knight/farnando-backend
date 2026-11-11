"""In-app notifications. Text is generic and never carries clinical details."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class NotificationType(models.TextChoices):
    EVALUATION_STATUS_CHANGED = "EVALUATION_STATUS_CHANGED", "Evaluation status changed"
    LABS_REQUESTED = "LABS_REQUESTED", "Labs requested"
    CONSULTATION_CONFIRMED = "CONSULTATION_CONFIRMED", "Consultation confirmed"
    CONSULTATION_DECLINED = "CONSULTATION_DECLINED", "Consultation declined"
    CONSULTATION_REMINDER = "CONSULTATION_REMINDER", "Consultation reminder"
    CONSULTATION_CANCELLED = "CONSULTATION_CANCELLED", "Consultation cancelled"
    NEW_MESSAGE = "NEW_MESSAGE", "New message"
    ORDER_STATUS_CHANGED = "ORDER_STATUS_CHANGED", "Order status changed"
    DOSE_REMINDER = "DOSE_REMINDER", "Dose reminder"
    REFILL_LOW = "REFILL_LOW", "Refill low"
    SUBSCRIPTION_RENEWED = "SUBSCRIPTION_RENEWED", "Subscription renewed"
    SUBSCRIPTION_PAYMENT_FAILED = "SUBSCRIPTION_PAYMENT_FAILED", "Subscription payment failed"
    SUPPORT_TICKET_REPLY = "SUPPORT_TICKET_REPLY", "Support ticket reply"
    TREATMENT_UPDATED = "TREATMENT_UPDATED", "Treatment updated"
    # Clinician types (Part 4, A6).
    NEW_EVALUATION_IN_QUEUE = "NEW_EVALUATION_IN_QUEUE", "New evaluation in queue"
    EVALUATION_LABS_UPLOADED = "EVALUATION_LABS_UPLOADED", "Evaluation labs uploaded"
    CONSULTATION_REQUESTED = "CONSULTATION_REQUESTED", "Consultation requested"
    CONSULTATION_CANCELLED_BY_PATIENT = (
        "CONSULTATION_CANCELLED_BY_PATIENT",
        "Consultation cancelled by patient",
    )
    CONSULTATION_RESCHEDULED_BY_PATIENT = (
        "CONSULTATION_RESCHEDULED_BY_PATIENT",
        "Consultation rescheduled by patient",
    )
    RENEWAL_REQUESTED = "RENEWAL_REQUESTED", "Renewal requested"
    CREDENTIALS_VERIFIED = "CREDENTIALS_VERIFIED", "Credentials verified"
    CREDENTIALS_REJECTED = "CREDENTIALS_REJECTED", "Credentials rejected"


class Notification(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=40, choices=NotificationType.choices)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default="")
    target_kind = models.CharField(max_length=40, blank=True, default="")
    target_id = models.UUIDField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.type}"
