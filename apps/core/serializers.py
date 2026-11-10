"""Serializers shared by the API and its documentation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers

from apps.core.exceptions import UnknownField


class StrictFieldsMixin:
    """Reject unknown write fields, as required by the public contract."""

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            declared = set(getattr(self, "fields", {}).keys())
            unknown = sorted(set(data.keys()) - declared)
            if unknown:
                raise UnknownField(str(unknown[0]))
        return super().to_internal_value(data)  # type: ignore[misc]


class StrictSerializer(StrictFieldsMixin, serializers.Serializer[Any]):
    pass


class StrictModelSerializer(StrictFieldsMixin, serializers.ModelSerializer[Any]):
    pass


class ErrorEnvelopeSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField()
    request_id = serializers.CharField()


class DetailSerializer(serializers.Serializer[Any]):
    detail = serializers.CharField()
