from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.db import models

from apps.consultations.models import (
    AvailabilityBlock,
    AvailabilityRule,
    ClinicianProfile,
    Consultation,
    ConsultationStatusHistory,
    ConsultationType,
)


@admin.register(ClinicianProfile)
class ClinicianProfileAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    formfield_overrides = {models.URLField: {"assume_scheme": "https"}}
    list_display = (
        "display_name",
        "care_role",
        "specialty",
        "license_number",
        "verification_status",
        "accepts_new_consultations",
    )
    list_filter = ("care_role", "verification_status", "accepts_new_consultations")
    list_editable = ("verification_status",)
    search_fields = ("display_name", "user__email", "license_label")
    raw_id_fields = ("user",)

    def save_model(self, request: Any, obj: ClinicianProfile, form: Any, change: bool) -> None:
        """Notify the physician when staff verify or reject credentials (Part 5 flow)."""

        previous = (
            ClinicianProfile.objects.filter(pk=obj.pk)
            .values_list("verification_status", flat=True)
            .first()
        )
        super().save_model(request, obj, form, change)
        if previous != obj.verification_status:
            from apps.clinical.onboarding import notify_verification_change

            notify_verification_change(obj)


@admin.register(AvailabilityRule)
class AvailabilityRuleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("physician", "weekday", "start_time", "end_time", "valid_from", "valid_until")
    list_filter = ("weekday",)
    raw_id_fields = ("physician",)


@admin.register(AvailabilityBlock)
class AvailabilityBlockAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("physician", "start", "end", "reason")
    raw_id_fields = ("physician",)


@admin.register(ConsultationType)
class ConsultationTypeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code", "name", "duration_minutes", "is_initial", "is_active", "position")
    list_editable = ("is_active", "position")


class ConsultationStatusHistoryInline(admin.TabularInline):  # type: ignore[type-arg]
    model = ConsultationStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "actor", "created_at")
    fields = readonly_fields


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    formfield_overrides = {models.URLField: {"assume_scheme": "https"}}
    list_display = ("start", "patient", "physician", "consultation_type", "status")
    list_filter = ("status", "consultation_type")
    raw_id_fields = ("patient", "physician", "assessment")
    readonly_fields = ("created_at", "updated_at", "video_room_name", "video_room_url")
    exclude = ("patient_note", "cancellation_reason")
    inlines = [ConsultationStatusHistoryInline]
