from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.payments.models import SavedPaymentMethod


class PaymentMethodSerializer(serializers.ModelSerializer[SavedPaymentMethod]):
    class Meta:
        model = SavedPaymentMethod
        fields = ["id", "brand", "last4", "exp_month", "exp_year", "is_default"]
        read_only_fields = fields


class PaymentMethodCreateSerializer(StrictSerializer):
    provider_token = serializers.CharField(max_length=200)


class WebhookAckSerializer(serializers.Serializer[Any]):
    received = serializers.BooleanField()
