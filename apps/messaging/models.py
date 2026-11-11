"""Patient <-> care-team threads. Bodies are encrypted at rest.

The schema is transport-agnostic: v1 polls over REST, and a WebSocket layer can
later push the same ``Message`` rows without a migration.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.encryption import EncryptedTextField
from apps.core.models import BaseModel


class MessageSenderRole(models.TextChoices):
    PATIENT = "PATIENT", "Patient"
    PHYSICIAN = "PHYSICIAN", "Physician"
    NURSE = "NURSE", "Nurse"


class MessageThread(BaseModel):
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="patient_threads"
    )
    participant = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="care_threads"
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["patient", "participant"], name="messaging_one_thread_per_pair"
            )
        ]

    def __str__(self) -> str:
        return f"{self.patient_id}<->{self.participant_id}"


class Message(BaseModel):
    thread = models.ForeignKey(MessageThread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_messages"
    )
    sender_role = models.CharField(max_length=16, choices=MessageSenderRole.choices)
    body = EncryptedTextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["thread", "-created_at"]),
            models.Index(fields=["thread", "sender", "read_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.thread_id}:{self.sender_id}:{self.created_at}"
