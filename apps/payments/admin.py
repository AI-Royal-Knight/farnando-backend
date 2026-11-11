from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.payments.models import SavedPaymentMethod, WebhookEvent


@admin.register(SavedPaymentMethod)
class SavedPaymentMethodAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "brand", "last4", "is_default", "is_deleted")
    list_filter = ("is_deleted", "brand")
    raw_id_fields = ("user",)
    readonly_fields = ("provider_card_id",)


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Immutable raw events: viewable, never editable."""

    list_display = ("provider", "event_type", "provider_event_id", "received_at", "processed_at")
    list_filter = ("provider", "event_type")
    search_fields = ("provider_event_id",)

    def has_add_permission(self, request: Any) -> bool:
        return False

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False
