"""Contact messages and the append-only Peruvian complaints book."""

from __future__ import annotations

import uuid

from django.db import models

from apps.catalog.models import ComplaintCategory


class ContactMessage(models.Model):
    class Status(models.TextChoices):
        NEW = "NEW", "New"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RESOLVED = "RESOLVED", "Resolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    message = models.TextField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.NEW, db_index=True
    )
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email}:{self.status}"


class ComplaintSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.year}:{self.last_number}"


class ComplaintBookEntry(models.Model):
    class FilingType(models.TextChoices):
        RECLAMO = "RECLAMO", "Reclamo / Claim"
        QUEJA = "QUEJA", "Queja / Feedback"

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", "Received"
        IN_REVIEW = "IN_REVIEW", "In review"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"

    class DocumentType(models.TextChoices):
        DNI = "DNI", "DNI"
        CE = "CE", "Carné de extranjería"
        PASSPORT = "PASSPORT", "Passport"
        RUC = "RUC", "RUC"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint_number = models.CharField(max_length=32, unique=True)
    year = models.PositiveIntegerField(db_index=True)
    sequence = models.PositiveIntegerField()
    full_name = models.CharField(max_length=120)
    email = models.EmailField(db_index=True)
    category = models.ForeignKey(ComplaintCategory, on_delete=models.PROTECT)
    filing_type = models.CharField(max_length=16, choices=FilingType.choices)
    description = models.TextField()
    order_reference = models.CharField(max_length=64, blank=True)
    document_type = models.CharField(max_length=16, choices=DocumentType.choices, blank=True)
    document_number = models.CharField(max_length=32, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )
    response_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    response_due_at = models.DateTimeField(db_index=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["year", "sequence"], name="support_complaint_year_sequence"
            )
        ]
        indexes = [
            models.Index(fields=["complaint_number", "email"], name="support_complaint_lookup_idx")
        ]

    def __str__(self) -> str:
        return self.complaint_number
