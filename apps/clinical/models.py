"""Clinician onboarding, uploads and the clinical record (contract Part 4)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.encryption import EncryptedTextField
from apps.core.models import BaseModel


class InvitationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    EXPIRED = "EXPIRED", "Expired"
    REVOKED = "REVOKED", "Revoked"


class UploadStatus(models.TextChoices):
    PENDING_UPLOAD = "PENDING_UPLOAD", "Pending upload"
    SCANNING = "SCANNING", "Scanning"
    READY = "READY", "Ready"
    CLEAN = "CLEAN", "Clean"
    REJECTED = "REJECTED", "Rejected"


class NoteKind(models.TextChoices):
    CLINICAL = "CLINICAL", "Clinical"
    ADMINISTRATIVE = "ADMINISTRATIVE", "Administrative"


class Specialty(BaseModel):
    name = models.CharField(max_length=120, unique=True)
    requires_registration_number = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]
        verbose_name_plural = "specialties"

    def __str__(self) -> str:
        return self.name


class PhysicianInvitation(BaseModel):
    """Single-use invitation link. Only the SHA-256 of the token is stored."""

    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=12, choices=InvitationStatus.choices, default=InvitationStatus.PENDING
    )
    expires_at = models.DateTimeField()
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_physician_invitations",
    )
    accepted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email}:{self.status}"


class AvatarUpload(BaseModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="avatar_uploads",
    )
    invitation = models.ForeignKey(
        PhysicianInvitation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="avatar_uploads",
    )
    storage_key = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=40)
    size_bytes = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=UploadStatus.choices, default=UploadStatus.PENDING_UPLOAD
    )

    def __str__(self) -> str:
        return self.storage_key


class PrescriptionDocument(BaseModel):
    """Optional signed prescription PDF uploaded by the physician."""

    evaluation = models.ForeignKey(
        "assessments.Evaluation", on_delete=models.CASCADE, related_name="prescription_documents"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    storage_key = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=40, default="application/pdf")
    size_bytes = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=UploadStatus.choices, default=UploadStatus.PENDING_UPLOAD
    )
    scanned_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.storage_key


class ClinicalNote(BaseModel):
    """Append-only. Corrections are new notes; there is no edit or delete."""

    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clinical_notes"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="authored_notes"
    )
    kind = models.CharField(max_length=16, choices=NoteKind.choices)
    body = EncryptedTextField()
    consultation = models.ForeignKey(
        "consultations.Consultation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notes",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.patient_id}:{self.kind}"
