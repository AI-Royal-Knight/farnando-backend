"""Admin views for support records; complaint originals are append-only."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.support.models import ComplaintBookEntry, ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("email", "full_name", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("email", "full_name")
    readonly_fields = (
        "id",
        "full_name",
        "email",
        "message",
        "source_ip",
        "user_agent",
        "created_at",
    )


@admin.register(ComplaintBookEntry)
class ComplaintBookEntryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "complaint_number",
        "email",
        "filing_type",
        "status",
        "created_at",
        "response_due_at",
    )
    list_filter = ("status", "filing_type", "category")
    search_fields = ("complaint_number", "email", "full_name")
    readonly_fields = (
        "id",
        "complaint_number",
        "year",
        "sequence",
        "full_name",
        "email",
        "category",
        "filing_type",
        "description",
        "order_reference",
        "document_type",
        "document_number",
        "phone",
        "created_at",
        "response_due_at",
        "source_ip",
        "user_agent",
    )

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False
