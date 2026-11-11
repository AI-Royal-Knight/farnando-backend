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
