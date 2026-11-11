"""Products and patient treatments (created by physicians in Part 4)."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.encryption import EncryptedTextField
from apps.core.models import BaseModel


class TreatmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    COMPLETED = "COMPLETED", "Completed"
    DISCONTINUED = "DISCONTINUED", "Discontinued"


class DoseFrequency(models.TextChoices):
    DAILY = "DAILY", "Daily"
    WEEKLY = "WEEKLY", "Weekly"
    EVERY_TWO_WEEKS = "EVERY_TWO_WEEKS", "Every two weeks"
    MONTHLY = "MONTHLY", "Monthly"
    AS_NEEDED = "AS_NEEDED", "As needed"


class TimeOfDay(models.TextChoices):
    MORNING = "MORNING", "Morning"
    AFTERNOON = "AFTERNOON", "Afternoon"
    EVENING = "EVENING", "Evening"
    ANY = "ANY", "Any"


class AdministrationRoute(models.TextChoices):
    SUBCUTANEOUS = "SUBCUTANEOUS", "Subcutaneous"
    ORAL = "ORAL", "Oral"
    SUBLINGUAL = "SUBLINGUAL", "Sublingual"
    TOPICAL = "TOPICAL", "Topical"
    INTRAMUSCULAR = "INTRAMUSCULAR", "Intramuscular"


class RenewalRequestStatus(models.TextChoices):
    REQUESTED = "REQUESTED", "Requested"
    APPROVED = "APPROVED", "Approved"
    DENIED = "DENIED", "Denied"


class Product(BaseModel):
    """Catalog item (Part 5 manages it). Images must be mockups, not real labels."""

    code = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    image_url = models.URLField(max_length=500, blank=True, default="")
    route = models.CharField(
        max_length=16, choices=AdministrationRoute.choices, blank=True, default=""
    )
    safety_information_markdown = models.TextField(
        blank=True, default="", help_text="[MEDICAL REVIEW] label content and warnings."
    )
    is_active = models.BooleanField(default=True, db_index=True)
    # Part 4 prescribing guardrails ([MEDICAL REVIEW], admin-managed).
    active_ingredient = models.CharField(max_length=120, blank=True, default="")
    form = models.CharField(max_length=60, blank=True, default="")
    allowed_doses = models.JSONField(default=list, blank=True)
    starting_doses = models.JSONField(default=list, blank=True)
    default_frequency = models.CharField(
        max_length=16, choices=DoseFrequency.choices, default=DoseFrequency.WEEKLY
    )
    max_refills = models.PositiveSmallIntegerField(default=0)
    programs = models.ManyToManyField("assessments.Program", blank=True, related_name="products")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Treatment(BaseModel):
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="treatments"
    )
    program = models.ForeignKey(
        "assessments.Program", on_delete=models.PROTECT, related_name="treatments"
    )
    evaluation = models.ForeignKey(
        "assessments.Evaluation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="treatments",
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, null=True, blank=True, related_name="treatments"
    )
    prescribing_physician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="prescribed_treatments"
    )
    status = models.CharField(
        max_length=16, choices=TreatmentStatus.choices, default=TreatmentStatus.ACTIVE
    )
    title = models.CharField(max_length=160)
    medication_name = models.CharField(max_length=160)
    strength_label = models.CharField(max_length=80, blank=True, default="")
    route = models.CharField(max_length=16, choices=AdministrationRoute.choices)
    frequency = models.CharField(max_length=16, choices=DoseFrequency.choices)
    day_of_week = models.PositiveSmallIntegerField(null=True, blank=True)
    time_of_day = models.CharField(max_length=10, choices=TimeOfDay.choices, default=TimeOfDay.ANY)
    schedule_label = models.CharField(max_length=120, blank=True, default="")
    dose_value = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("0"))
    dose_unit = models.CharField(max_length=16, default="mg")
    dose_label = models.CharField(max_length=80, blank=True, default="")
    started_at = models.DateField()
    refills_total = models.PositiveSmallIntegerField(null=True, blank=True)
    refills_remaining = models.PositiveSmallIntegerField(null=True, blank=True)
    prescription_expires_at = models.DateField(null=True, blank=True)
    prescription_pdf_key = models.CharField(max_length=255, blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    duration_weeks = models.PositiveSmallIntegerField(null=True, blank=True)
    prescription = models.ForeignKey(
        "assessments.Prescription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="treatments",
    )
    next_check_in_date = models.DateField(null=True, blank=True)
    next_check_in_consultation = models.ForeignKey(
        "consultations.Consultation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-started_at", "-created_at"]
        indexes = [models.Index(fields=["patient", "status"])]

    def __str__(self) -> str:
        return f"{self.patient_id}:{self.title}"

    @property
    def has_refills(self) -> bool:
        return self.refills_total is not None and self.refills_remaining is not None


class TreatmentRenewalRequest(BaseModel):
    treatment = models.ForeignKey(
        Treatment, on_delete=models.CASCADE, related_name="renewal_requests"
    )
    status = models.CharField(
        max_length=16,
        choices=RenewalRequestStatus.choices,
        default=RenewalRequestStatus.REQUESTED,
    )
    note = EncryptedTextField(blank=True, default="")
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decision_note = EncryptedTextField(blank=True, default="")
    patient_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["treatment"],
                condition=models.Q(resolved_at__isnull=True),
                name="treatments_one_pending_renewal",
            )
        ]

    def __str__(self) -> str:
        return f"renewal:{self.treatment_id}"


class TreatmentVersion(BaseModel):
    """Immutable dose/schedule history. Version 1 is created at approval."""

    treatment = models.ForeignKey(Treatment, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    dose_value = models.DecimalField(max_digits=8, decimal_places=3)
    dose_unit = models.CharField(max_length=16)
    dose_label = models.CharField(max_length=80)
    frequency = models.CharField(max_length=16, choices=DoseFrequency.choices)
    day_of_week = models.PositiveSmallIntegerField(null=True, blank=True)
    time_of_day = models.CharField(max_length=10, choices=TimeOfDay.choices)
    instructions = models.TextField(blank=True, default="")
    effective_from = models.DateField()
    applied_at = models.DateTimeField(null=True, blank=True)
    reason = EncryptedTextField(blank=True, default="")
    patient_note = models.TextField(blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["version"]
        constraints = [
            models.UniqueConstraint(
                fields=["treatment", "version"], name="treatments_version_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.treatment_id}:v{self.version}"
