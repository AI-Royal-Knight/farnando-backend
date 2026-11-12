"""Public content and catalog-adjacent configuration."""

from __future__ import annotations

import uuid

from django.db import models


class SiteSettings(models.Model):
    """Singleton edited by staff; the API never hardcodes public values."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    social_links = models.JSONField(default=list)
    legal_entity_name = models.CharField(max_length=200, blank=True)
    messaging_safety_notice = models.TextField(
        default=(
            "Messages are not monitored 24/7. In an emergency contact local "
            "emergency services (SAMU 106)."
        ),
        help_text="[MEDICAL REVIEW] wording and emergency numbers.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "site settings"

    def __str__(self) -> str:
        return "Site settings"

    @classmethod
    def load(cls) -> SiteSettings:
        instance = cls.objects.first()
        if instance is None:
            instance = cls.objects.create()
        return instance


class FAQ(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.CharField(max_length=300)
    answer_markdown = models.TextField()
    position = models.PositiveIntegerField(default=0, db_index=True)
    is_published = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "FAQ"

    def __str__(self) -> str:
        return self.question


class Testimonial(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author_name = models.CharField(max_length=80)
    rating = models.PositiveSmallIntegerField()
    body = models.TextField()
    published_at = models.DateTimeField(db_index=True)
    is_published = models.BooleanField(default=False, db_index=True)
    patient_consent_recorded = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published_at", "id"]

    def __str__(self) -> str:
        return self.author_name


class PublicStats(models.Model):
    """Marketing claims are admin-editable, nullable and evidence-reviewed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sustained_weight_loss_percent = models.FloatField(null=True, blank=True)
    consultations_delivered = models.PositiveIntegerField(null=True, blank=True)
    average_reply_minutes = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "public stats"

    def __str__(self) -> str:
        return "Public stats"

    @classmethod
    def load(cls) -> PublicStats:
        instance = cls.objects.first()
        if instance is None:
            instance = cls.objects.create()
        return instance


class ComplaintCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True, db_index=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "code"]
        verbose_name_plural = "complaint categories"

    def __str__(self) -> str:
        return self.code


class TicketCategory(models.Model):
    """Support-ticket categories (Part 5, 6.1 and 9.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=120)
    requires_clinical_staff = models.BooleanField(
        default=False, help_text="[MEDICAL REVIEW] visible to clinical staff only."
    )
    is_active = models.BooleanField(default=True, db_index=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "code"]
        verbose_name_plural = "ticket categories"

    def __str__(self) -> str:
        return self.code


class LabPanel(models.Model):
    """Lab-panel catalog used by lab requests (Part 5, 9.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "code"]

    def __str__(self) -> str:
        return self.code


class PlatformSettings(models.Model):
    """Runtime settings groups edited by admins (Part 5, 9.5).

    Each field holds one group as JSON. Missing keys fall back to the Django
    settings defaults in ``apps.catalog.runtime``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evaluation = models.JSONField(default=dict, blank=True)
    booking = models.JSONField(default=dict, blank=True)
    complaints = models.JSONField(default=dict, blank=True)
    support = models.JSONField(default=dict, blank=True)
    payments = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "platform settings"

    def __str__(self) -> str:
        return "Platform settings"

    @classmethod
    def load(cls) -> PlatformSettings:
        instance = cls.objects.first()
        if instance is None:
            instance = cls.objects.create()
        return instance


class AssetPurpose(models.TextChoices):
    CONTENT_IMAGE = "CONTENT_IMAGE", "Content image"
    PRODUCT_IMAGE = "PRODUCT_IMAGE", "Product image"


class AssetStatus(models.TextChoices):
    PENDING_UPLOAD = "PENDING_UPLOAD", "Pending upload"
    READY = "READY", "Ready"
    REJECTED = "REJECTED", "Rejected"


class AssetUpload(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=20, choices=AssetPurpose.choices)
    storage_key = models.CharField(max_length=255, unique=True)
    content_type = models.CharField(max_length=40)
    size_bytes = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=AssetStatus.choices, default=AssetStatus.PENDING_UPLOAD
    )
    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.storage_key


class ContentCategory(models.TextChoices):
    TREATMENT = "TREATMENT", "Treatment"
    MEDICINE = "MEDICINE", "Medicine"
    PROGRAM = "PROGRAM", "Program"


class ContentStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"


class ContentItem(models.Model):
    """Landing-page cards managed by admins (Part 5, 9.1; public via B1)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=120)
    accent_phrase = models.CharField(max_length=120, blank=True, default="")
    description = models.TextField()
    category = models.CharField(max_length=16, choices=ContentCategory.choices)
    status = models.CharField(
        max_length=12, choices=ContentStatus.choices, default=ContentStatus.DRAFT, db_index=True
    )
    product_image = models.ForeignKey(
        AssetUpload, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    lifestyle_images = models.ManyToManyField(AssetUpload, blank=True, related_name="+")
    benefits = models.JSONField(default=list, blank=True)
    pricing = models.JSONField(null=True, blank=True)
    cta_text = models.CharField(max_length=40, blank=True, default="")
    program = models.ForeignKey(
        "assessments.Program", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    position = models.PositiveIntegerField(default=0, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "-created_at"]

    def __str__(self) -> str:
        return self.title
