from __future__ import annotations

from django.contrib import admin
from django.db import models

from apps.treatments.models import Product, Treatment, TreatmentRenewalRequest


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    formfield_overrides = {models.URLField: {"assume_scheme": "https"}}
    list_display = ("name", "code", "route", "is_active")
    list_filter = ("is_active", "route")
    search_fields = ("name", "code")


@admin.register(Treatment)
class TreatmentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("patient", "title", "status", "frequency", "refills_remaining", "started_at")
    list_filter = ("status", "frequency")
    raw_id_fields = (
        "patient",
        "evaluation",
        "product",
        "prescribing_physician",
        "next_check_in_consultation",
    )


@admin.register(TreatmentRenewalRequest)
class TreatmentRenewalRequestAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("treatment", "status", "created_at", "resolved_at")
    raw_id_fields = ("treatment",)
    exclude = ("note",)
