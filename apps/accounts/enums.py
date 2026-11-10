"""Account-level enumerations shared by models, permissions and the API layer."""

from __future__ import annotations

from django.db import models


class Role(models.TextChoices):
    PATIENT = "PATIENT", "Patient"
    PHYSICIAN = "PHYSICIAN", "Physician"
    STAFF = "STAFF", "Staff"
    ADMIN = "ADMIN", "Administrator"
