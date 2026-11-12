"""Clinician profiles, availability, consultation types and consultations."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.encryption import EncryptedTextField
from apps.core.models import BaseModel


class CareRole(models.TextChoices):
    PHYSICIAN = "PHYSICIAN", "Physician"
    NURSE = "NURSE", "Nurse"


class PhysicianVerificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    VERIFIED = "VERIFIED", "Verified"
    SUSPENDED = "SUSPENDED", "Suspended"
    REJECTED = "REJECTED", "Rejected"


class AvailabilityKind(models.TextChoices):
    AVAILABLE = "AVAILABLE", "Available"
    BLOCKED = "BLOCKED", "Blocked"


class TimeBlockKind(models.TextChoices):
    BLOCKED = "BLOCKED", "Blocked"
    EXTRA_AVAILABLE = "EXTRA_AVAILABLE", "Extra available"


class ConsultationStatus(models.TextChoices):
    REQUESTED = "REQUESTED", "Requested"
    CONFIRMED = "CONFIRMED", "Confirmed"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Expired"
    NO_SHOW = "NO_SHOW", "No show"


OPEN_CONSULTATION_STATUSES = (
    ConsultationStatus.REQUESTED,
    ConsultationStatus.CONFIRMED,
    ConsultationStatus.IN_PROGRESS,
)


class ClinicianProfile(BaseModel):
    """Public-facing data for a physician (PHYSICIAN user) or nurse (STAFF user)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clinician_profile"
    )
    care_role = models.CharField(max_length=16, choices=CareRole.choices)
    display_name = models.CharField(max_length=160, help_text='e.g. "Dr. Marcus Vance, MD"')
    headline = models.CharField(max_length=200, blank=True, default="")
    specialty = models.CharField(max_length=120, blank=True, default="")
    license_label = models.CharField(
        max_length=60, blank=True, default="", help_text='e.g. "CMP 12345"'
    )
    avatar_url = models.URLField(max_length=500, blank=True, default="")
    accepts_new_consultations = models.BooleanField(default=True)
    timezone = models.CharField(max_length=64, default="America/Lima")
    # Part 4 physician profile.
    verification_status = models.CharField(
        max_length=12,
        choices=PhysicianVerificationStatus.choices,
        default=PhysicianVerificationStatus.PENDING,
        db_index=True,
    )
    verification_note = models.TextField(blank=True, default="")
    bio = models.TextField(blank=True, default="")
    specialty_ref = models.ForeignKey(
        "clinical.Specialty",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clinicians",
    )
    license_number = models.CharField(max_length=20, null=True, blank=True, unique=True)
    specialty_registration_number = models.CharField(max_length=20, blank=True, default="")
    phone = models.CharField(max_length=16, blank=True, default="")
    languages = models.JSONField(default=list, blank=True)
    avatar_key = models.CharField(max_length=255, blank=True, default="")
    preferred_weight_unit = models.CharField(max_length=2, default="KG")

    class Meta:
        ordering = ["display_name"]

    def __str__(self) -> str:
        return self.display_name


class AvailabilityRule(BaseModel):
    """Weekly working window in the physician's timezone (managed in Part 4)."""

    physician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="availability_rules"
    )
    kind = models.CharField(
        max_length=12, choices=AvailabilityKind.choices, default=AvailabilityKind.AVAILABLE
    )
    weekday = models.PositiveSmallIntegerField(help_text="1 = Monday ... 7 = Sunday")
    start_time = models.TimeField()
    end_time = models.TimeField()
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    label = models.CharField(max_length=60, blank=True, default="")
    consultation_types = models.ManyToManyField(
        "consultations.ConsultationType",
        blank=True,
        related_name="+",
        help_text="Empty = every consultation type. Ignored for BLOCKED.",
    )

    class Meta:
        ordering = ["weekday", "start_time"]

    def __str__(self) -> str:
        return f"{self.physician_id}:{self.weekday}:{self.start_time}-{self.end_time}"


class AvailabilityBlock(BaseModel):
    """Blocked interval (lunch break, time off) in UTC."""

    physician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="availability_blocks"
    )
    kind = models.CharField(
        max_length=16, choices=TimeBlockKind.choices, default=TimeBlockKind.BLOCKED
    )
    start = models.DateTimeField()
    end = models.DateTimeField()
    reason = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        ordering = ["start"]
        indexes = [models.Index(fields=["physician", "start", "end"])]

    def __str__(self) -> str:
        return f"{self.physician_id}:{self.start}-{self.end}"


class ConsultationType(BaseModel):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    is_initial = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self) -> str:
        return self.code


class Consultation(BaseModel):
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consultations"
    )
    physician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="physician_consultations"
    )
    consultation_type = models.ForeignKey(
        ConsultationType, on_delete=models.PROTECT, related_name="consultations"
    )
    assessment = models.ForeignKey(
        "assessments.Assessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="consultations",
    )
    status = models.CharField(
        max_length=16,
        choices=ConsultationStatus.choices,
        default=ConsultationStatus.REQUESTED,
        db_index=True,
    )
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    timezone = models.CharField(max_length=64, default="America/Lima")
    patient_note = EncryptedTextField(blank=True, default="")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    patient_left_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = EncryptedTextField(blank=True, default="")
    video_room_name = models.CharField(max_length=120, blank=True, default="")
    video_room_url = models.URLField(max_length=500, blank=True, default="")
    reminder_24h_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_1h_sent_at = models.DateTimeField(null=True, blank=True)
    # Part 4 clinician side.
    agenda = models.CharField(max_length=300, blank=True, default="")
    physician_joined_at = models.DateTimeField(null=True, blank=True)
    rescheduled_by_physician_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    clinical_notes = EncryptedTextField(blank=True, default="")
    visit_summary = models.TextField(blank=True, default="")
    needs_staff_attention = models.BooleanField(default=False, db_index=True)
    follow_up = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["start"]
        constraints = [
            models.UniqueConstraint(
                fields=["physician", "start"],
                condition=models.Q(status__in=["REQUESTED", "CONFIRMED", "IN_PROGRESS"]),
                name="consultations_slot_held_once",
            )
        ]
        indexes = [
            models.Index(fields=["patient", "status", "start"]),
            models.Index(fields=["physician", "status", "start"]),
        ]

    def __str__(self) -> str:
        return f"{self.patient_id}:{self.start}:{self.status}"


class ConsultationStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    consultation = models.ForeignKey(
        Consultation, on_delete=models.CASCADE, related_name="status_history"
    )
    from_status = models.CharField(max_length=16, blank=True, default="")
    to_status = models.CharField(max_length=16)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = EncryptedTextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.consultation_id}:{self.from_status}->{self.to_status}"
