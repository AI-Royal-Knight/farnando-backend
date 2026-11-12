"""Contact messages and the append-only Peruvian complaints book."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.catalog.models import ComplaintCategory, TicketCategory
from apps.core.encryption import EncryptedTextField


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


class ComplaintResponse(models.Model):
    """Append-only staff response to a complaint (Part 5, 6.4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint = models.ForeignKey(
        ComplaintBookEntry, on_delete=models.PROTECT, related_name="responses"
    )
    body = models.TextField()
    resolution = models.CharField(max_length=16)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.complaint_id}:{self.resolution}"


class ComplaintHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint = models.ForeignKey(
        ComplaintBookEntry, on_delete=models.PROTECT, related_name="history"
    )
    from_status = models.CharField(max_length=16, blank=True, default="")
    to_status = models.CharField(max_length=16)
    note = models.TextField(blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.complaint_id}:{self.to_status}"


class TicketStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER", "Awaiting customer"
    RESOLVED = "RESOLVED", "Resolved"
    CLOSED = "CLOSED", "Closed"


class TicketPriority(models.TextChoices):
    NORMAL = "NORMAL", "Normal"
    URGENT = "URGENT", "Urgent"


class TicketSource(models.TextChoices):
    PATIENT = "PATIENT", "Patient"
    CONTACT_FORM = "CONTACT_FORM", "Contact form"
    STAFF = "STAFF", "Staff"


class TicketSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.year}:{self.last_number}"


class SupportTicket(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=32, unique=True)
    subject = models.CharField(max_length=120)
    status = models.CharField(
        max_length=20, choices=TicketStatus.choices, default=TicketStatus.OPEN, db_index=True
    )
    priority = models.CharField(
        max_length=8, choices=TicketPriority.choices, default=TicketPriority.NORMAL
    )
    category = models.ForeignKey(TicketCategory, on_delete=models.PROTECT, related_name="tickets")
    source = models.CharField(max_length=16, choices=TicketSource.choices)
    requester_name = models.CharField(max_length=120)
    requester_email = models.EmailField()
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    order = models.ForeignKey(
        "orders.Order", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["patient", "-created_at"]),
        ]

    def __str__(self) -> str:
        return self.reference


class TicketMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    author_role = models.CharField(max_length=10)
    body = EncryptedTextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.ticket_id}:{self.author_role}"


class TicketHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="history")
    event = models.CharField(max_length=24)
    from_value = models.CharField(max_length=64, blank=True, default="")
    to_value = models.CharField(max_length=64, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.ticket_id}:{self.event}"
