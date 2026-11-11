from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.notifications.models import Notification, NotificationType


class NotificationTargetSerializer(serializers.Serializer[Any]):
    kind = serializers.CharField()
    id = serializers.UUIDField(allow_null=True)


class NotificationSerializer(serializers.ModelSerializer[Notification]):
    type = serializers.ChoiceField(choices=NotificationType.choices)
    body = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "type", "title", "body", "target", "is_read", "created_at"]
        read_only_fields = fields

    def get_body(self, obj: Notification) -> str | None:
        return obj.body or None

    @staticmethod
    def _target(obj: Notification) -> dict[str, Any] | None:
        if not obj.target_kind:
            return None
        return {"kind": obj.target_kind, "id": str(obj.target_id) if obj.target_id else None}

    def get_target(self, obj: Notification) -> dict[str, Any] | None:
        return self._target(obj)


class CountSerializer(serializers.Serializer[Any]):
    count = serializers.IntegerField()
