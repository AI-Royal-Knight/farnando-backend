"""Message content is not exposed in the admin. Staff access is the audited Part 5 path."""

from __future__ import annotations

from django.contrib import admin

from apps.messaging.models import MessageThread


@admin.register(MessageThread)
class MessageThreadAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("patient", "participant", "last_message_at", "created_at")
    raw_id_fields = ("patient", "participant")
