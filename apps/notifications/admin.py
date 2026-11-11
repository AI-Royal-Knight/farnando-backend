from __future__ import annotations

from django.contrib import admin

from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "type", "is_read", "created_at")
    list_filter = ("type", "is_read")
    raw_id_fields = ("user",)
