"""Saved cards (provider references only) and raw provider webhook events."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class WebhookProvider(models.TextChoices):
    CULQI = "CULQI", "Culqi"
    DAILY = "DAILY", "Daily"


class SavedPaymentMethod(BaseModel):
    """A card stored at the provider. The API never sees or returns card numbers."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_methods"
    )
    provider = models.CharField(max_length=16, default="CULQI")
    provider_card_id = models.CharField(max_length=120)
    brand = models.CharField(max_length=30)
    last4 = models.CharField(max_length=4)
    exp_month = models.PositiveSmallIntegerField()
    exp_year = models.PositiveSmallIntegerField()
    is_default = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True, is_deleted=False),
                name="payments_one_default_method",
            )
        ]

    def __str__(self) -> str:
        return f"{self.brand} ****{self.last4}"


class WebhookEvent(models.Model):
    """Immutable raw event log. Rows are never updated except processing metadata."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=16, choices=WebhookProvider.choices)
    provider_event_id = models.CharField(max_length=160)
    event_type = models.CharField(max_length=120)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_event_id"], name="payments_webhook_event_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.provider_event_id}"
