"""Patient profile, preferences, delivery addresses and Peru ubigeo reference data."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class UbigeoLevel(models.TextChoices):
    DEPARTMENT = "DEPARTMENT", "Department"
    PROVINCE = "PROVINCE", "Province"
    DISTRICT = "DISTRICT", "District"


class Language(models.TextChoices):
    ES = "es", "Spanish"
    EN = "en", "English"


class Ubigeo(models.Model):
    """INEI geographic code. Departments use 2 digits, provinces 4, districts 6."""

    code = models.CharField(max_length=6, primary_key=True)
    name = models.CharField(max_length=120)
    level = models.CharField(max_length=12, choices=UbigeoLevel.choices, db_index=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    # Only meaningful for districts; null for departments and provinces.
    is_deliverable = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["code"]
        indexes = [models.Index(fields=["level", "parent"])]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class PatientProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="patient_profile"
    )
    phone = models.CharField(max_length=16, blank=True, default="")
    default_address = models.ForeignKey(
        "patients.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    weight_unit = models.CharField(max_length=2, default="KG")
    height_unit = models.CharField(max_length=2, default="CM")
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.ES)
    timezone = models.CharField(max_length=64, default="America/Lima")
    email_messages = models.BooleanField(default=True)
    email_orders = models.BooleanField(default=True)
    email_reminders = models.BooleanField(default=True)
    email_marketing = models.BooleanField(default=False)
    marketing_opt_in_at = models.DateTimeField(null=True, blank=True)
    assigned_physician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_care_patients",
        help_text="Care physician set by staff (Part 5, 4.3).",
    )
    assigned_nurse = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_patients",
        help_text="STAFF user with a NURSE clinician profile.",
    )

    def __str__(self) -> str:
        return f"profile:{self.user_id}"


class Address(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses"
    )
    label = models.CharField(max_length=30, blank=True, default="")
    recipient_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=16)
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=100, blank=True, default="")
    reference = models.CharField(max_length=200, blank=True, default="")
    ubigeo = models.ForeignKey(Ubigeo, on_delete=models.PROTECT, related_name="addresses")
    postal_code = models.CharField(max_length=10, blank=True, default="")
    country = models.CharField(max_length=2, default="PE")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_default=True),
                name="patients_one_default_address",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.label or self.line1}"

    @property
    def is_deliverable(self) -> bool:
        return bool(self.ubigeo.is_deliverable)
