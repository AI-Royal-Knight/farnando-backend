"""Part 3 section 6 shapes."""

from __future__ import annotations

import json
from typing import Any

from rest_framework import serializers

from apps.assessments.serializers import MoneySerializer, PaymentSerializer
from apps.assessments.services import money
from apps.core.serializers import StrictSerializer
from apps.orders.models import (
    BillingDocumentType,
    BillingType,
    Order,
    OrderStatus,
    Package,
    Subscription,
    SubscriptionStatus,
)
from apps.orders.services import PATIENT_CANCELLABLE, STATUS_LABELS
from apps.payments.serializers import PaymentMethodSerializer


class PackageItemOutSerializer(serializers.Serializer[Any]):
    product_id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField()
    quantity = serializers.IntegerField()
    image_url = serializers.CharField(allow_null=True)


class PackageSerializer(serializers.ModelSerializer[Package]):
    billing_type = serializers.ChoiceField(choices=BillingType.choices)
    price = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()

    class Meta:
        model = Package
        fields = [
            "id",
            "name",
            "description",
            "supply_duration_weeks",
            "billing_type",
            "billing_interval_months",
            "price",
            "items",
        ]
        read_only_fields = fields

    def get_price(self, obj: Package) -> dict[str, str]:
        return money(obj.price, obj.currency)

    def get_items(self, obj: Package) -> list[dict[str, Any]]:
        return [
            {
                "product_id": str(item.product_id),
                "name": item.product.name,
                "description": item.description,
                "quantity": item.quantity,
                "image_url": item.product.image_url or None,
            }
            for item in obj.items.all()
        ]


class PaymentInputSerializer(StrictSerializer):
    method = serializers.ChoiceField(choices=[("CULQI", "Culqi"), ("SAVED_METHOD", "Saved")])
    provider_token = serializers.CharField(max_length=200, required=False, allow_blank=True)
    payment_method_id = serializers.UUIDField(required=False, allow_null=True)
    save_payment_method = serializers.BooleanField(required=False, default=False)


class BillingInputSerializer(StrictSerializer):
    document_type = serializers.ChoiceField(choices=BillingDocumentType.choices)
    document_number = serializers.CharField(max_length=20)
    business_name = serializers.CharField(max_length=200, required=False, allow_null=True)


class QuoteInputSerializer(StrictSerializer):
    treatment_id = serializers.UUIDField()
    package_id = serializers.UUIDField()
    shipping_address_id = serializers.UUIDField()
    billing = BillingInputSerializer(required=False, allow_null=True)
    notes = serializers.CharField(max_length=300, required=False, allow_blank=True)


class OrderCreateSerializer(QuoteInputSerializer):
    payment = PaymentInputSerializer()


class PayOrderSerializer(PaymentInputSerializer):
    pass


class QuoteItemSerializer(serializers.Serializer[Any]):
    product_id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = MoneySerializer()
    image_url = serializers.CharField(allow_null=True)


class DeliveryDaysSerializer(serializers.Serializer[Any]):
    min = serializers.IntegerField()
    max = serializers.IntegerField()


class QuoteSerializer(serializers.Serializer[Any]):
    items = QuoteItemSerializer(many=True)
    subtotal = MoneySerializer()
    shipping = MoneySerializer()
    tax = MoneySerializer()
    total = MoneySerializer()
    estimated_delivery_days = DeliveryDaysSerializer()


class OrderListItemSerializer(serializers.ModelSerializer[Order]):
    status = serializers.ChoiceField(choices=OrderStatus.choices)
    items = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "number",
            "placed_at",
            "items",
            "status",
            "status_label",
            "delivered_at",
            "estimated_delivery_date",
        ]
        read_only_fields = fields

    def get_items(self, obj: Order) -> list[dict[str, str]]:
        return [{"name": item.name, "description": item.description} for item in obj.items.all()]

    def get_status_label(self, obj: Order) -> str:
        return STATUS_LABELS.get(obj.status, obj.status)


class OrderSerializer(OrderListItemSerializer):
    subtotal = serializers.SerializerMethodField()
    shipping = serializers.SerializerMethodField()
    tax = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    shipping_address = serializers.JSONField(source="shipping_address_snapshot", read_only=True)
    tracking = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()
    subscription_id = serializers.UUIDField(read_only=True, allow_null=True)
    can_cancel = serializers.SerializerMethodField()
    billing_document = serializers.SerializerMethodField()

    class Meta(OrderListItemSerializer.Meta):
        fields = [
            *OrderListItemSerializer.Meta.fields,
            "subtotal",
            "shipping",
            "tax",
            "total",
            "shipping_address",
            "tracking",
            "timeline",
            "payment",
            "subscription_id",
            "can_cancel",
            "billing_document",
        ]
        read_only_fields = fields

    def get_items(self, obj: Order) -> list[dict[str, Any]]:
        return [
            {
                "product_id": str(item.product_id),
                "name": item.name,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": money(item.unit_price, obj.currency),
                "image_url": item.image_url or None,
            }
            for item in obj.items.all()
        ]

    def get_subtotal(self, obj: Order) -> dict[str, str]:
        return money(obj.subtotal, obj.currency)

    def get_shipping(self, obj: Order) -> dict[str, str]:
        return money(obj.shipping, obj.currency)

    def get_tax(self, obj: Order) -> dict[str, str]:
        return money(obj.tax, obj.currency)

    def get_total(self, obj: Order) -> dict[str, str]:
        return money(obj.total, obj.currency)

    def get_tracking(self, obj: Order) -> dict[str, Any] | None:
        if not obj.tracking_number:
            return None
        return {
            "carrier": obj.tracking_carrier,
            "tracking_number": obj.tracking_number,
            "tracking_url": obj.tracking_url or None,
        }

    def get_timeline(self, obj: Order) -> list[dict[str, Any]]:
        field = serializers.DateTimeField()
        return [
            {"status": row.to_status, "at": field.to_representation(row.created_at)}
            for row in obj.status_history.all()
        ]

    def get_payment(self, obj: Order) -> dict[str, Any] | None:
        payment = obj.payments.order_by("-created_at").first()
        return dict(PaymentSerializer(payment).data) if payment is not None else None

    def get_can_cancel(self, obj: Order) -> bool:
        return obj.status in PATIENT_CANCELLABLE

    def get_billing_document(self, obj: Order) -> dict[str, Any] | None:
        if not obj.billing_document_number:
            return None
        return {
            "type": obj.billing_document_type,
            "number": obj.billing_document_number,
            "download_url": None,
        }


def billing_details(order: Order) -> dict[str, Any] | None:
    return json.loads(order.billing) if order.billing else None


class OrderSummaryCountsSerializer(serializers.Serializer[Any]):
    total_count = serializers.IntegerField()
    active_count = serializers.IntegerField()
    delivered_count = serializers.IntegerField()


class ReasonInputSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class SubscriptionPackageSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    name = serializers.CharField()
    price = MoneySerializer()
    billing_interval_months = serializers.IntegerField(allow_null=True)


class SubscriptionSerializer(serializers.ModelSerializer[Subscription]):
    status = serializers.ChoiceField(choices=SubscriptionStatus.choices)
    package = serializers.SerializerMethodField()
    payment_method = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            "id",
            "status",
            "package",
            "current_period_start",
            "current_period_end",
            "next_billing_at",
            "next_shipment_at",
            "paused_until",
            "cancel_at_period_end",
            "payment_method",
            "created_at",
        ]
        read_only_fields = fields

    def get_package(self, obj: Subscription) -> dict[str, Any]:
        package = obj.package
        return {
            "id": str(package.pk),
            "name": package.name,
            "price": money(package.price, package.currency),
            "billing_interval_months": package.billing_interval_months,
        }

    def get_payment_method(self, obj: Subscription) -> dict[str, Any] | None:
        method = obj.payment_method
        if method is None or method.is_deleted:
            return None
        return dict(PaymentMethodSerializer(method).data)


class PauseInputSerializer(StrictSerializer):
    resume_on = serializers.DateField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


class SubscriptionCancelInputSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)
    feedback = serializers.CharField(max_length=1000, required=False, allow_blank=True)
