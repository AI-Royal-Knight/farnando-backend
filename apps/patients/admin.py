from __future__ import annotations

from django.contrib import admin

from apps.patients.models import Address, PatientProfile, Ubigeo


@admin.register(PatientProfile)
class PatientProfileAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "language", "timezone", "assigned_nurse", "created_at")
    search_fields = ("user__email", "user__full_name")
    raw_id_fields = ("user", "default_address", "assigned_nurse")
    readonly_fields = ("marketing_opt_in_at", "created_at", "updated_at")


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "label", "ubigeo", "is_default", "created_at")
    search_fields = ("user__email",)
    raw_id_fields = ("user", "ubigeo")


@admin.register(Ubigeo)
class UbigeoAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code", "name", "level", "parent", "is_deliverable")
    list_filter = ("level", "is_deliverable")
    search_fields = ("code", "name")
    list_editable = ("is_deliverable",)
    raw_id_fields = ("parent",)
