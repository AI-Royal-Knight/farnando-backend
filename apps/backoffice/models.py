"""Privacy requests and report exports (contract Part 5, sections 10 and 11)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.encryption import EncryptedTextField
from apps.core.models import BaseModel


class PrivacyRequestType(models.TextChoices):
    ACCESS_EXPORT = "ACCESS_EXPORT", "Access / export"
    RECTIFICATION = "RECTIFICATION", "Rectification"
    DELETION = "DELETION", "Deletion"


class PrivacyRequestStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    COMPLETED = "COMPLETED", "Completed"
    REJECTED = "REJECTED", "Rejected"


class PrivacyRequest(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="privacy_requests"
    )
    type = models.CharField(max_length=16, choices=PrivacyRequestType.choices)
    status = models.CharField(
        max_length=12,
        choices=PrivacyRequestStatus.choices,
        default=PrivacyRequestStatus.RECEIVED,
        db_index=True,
    )
    details = EncryptedTextField(blank=True, default="")
    response_note = models.TextField(blank=True, default="")
    due_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    export_key = models.CharField(max_length=255, blank=True, default="")
    export_expires_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.type}:{self.status}"


class ReportKind(models.TextChoices):
    OVERVIEW = "OVERVIEW", "Overview"
    REVENUE = "REVENUE", "Revenue"
    EVALUATIONS = "EVALUATIONS", "Evaluations"
    ORDERS = "ORDERS", "Orders"
    SUBSCRIPTIONS = "SUBSCRIPTIONS", "Subscriptions"


class ExportStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    READY = "READY", "Ready"
    FAILED = "FAILED", "Failed"


class ReportExport(BaseModel):
    report = models.CharField(max_length=16, choices=ReportKind.choices)
    date_from = models.DateField()
    date_to = models.DateField()
    format = models.CharField(max_length=8, default="CSV")
    status = models.CharField(
        max_length=8, choices=ExportStatus.choices, default=ExportStatus.PENDING
    )
    storage_key = models.CharField(max_length=255, blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.report}:{self.status}"
