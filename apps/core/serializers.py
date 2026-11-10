"""Serializers shared by the API documentation."""

from __future__ import annotations

from rest_framework import serializers


class ErrorEnvelopeSerializer(serializers.Serializer):  # type: ignore[type-arg]
    code = serializers.CharField()
    message = serializers.CharField()
    details = serializers.JSONField()
    request_id = serializers.CharField()


class DetailSerializer(serializers.Serializer):  # type: ignore[type-arg]
    detail = serializers.CharField()
