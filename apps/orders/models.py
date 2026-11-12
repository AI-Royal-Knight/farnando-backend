"""Packages, orders, order history and subscriptions."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import models

from apps.core.encryption import EncryptedTextField
from apps.core.models import BaseModel


class OrderStatus(models.TextChoices):
    PENDING_PAYMENT = "PENDING_PAYMENT", "Pending payment"
    PAYMENT_FAILED = "PAYMENT_FAILED", "Payment failed"
    PRESCRIPTION_VERIFIED = "PRESCRIPTION_VERIFIED", "Prescription verified"
    ON_HOLD = "ON_HOLD", "On hold"
    COMPOUNDING = "COMPOUNDING", "Compounding"
    IN_TRANSIT = "IN_TRANSIT", "In transit"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY", "Out for delivery"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"
    REFUNDED = "REFUNDED", "Refunded"


class OrderStatusGroup(models.TextChoices):
    ALL = "ALL", "All"
    ACTIVE = "ACTIVE", "Active"
    DELIVERED = "DELIVERED", "Delivered"


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    PAUSED = "PAUSED", "Paused"
    PAST_DUE = "PAST_DUE", "Past due"
    CANCELLED = "CANCELLED", "Cancelled"


class BillingType(models.TextChoices):
    ONE_TIME = "ONE_TIME", "One time"
    RECURRING = "RECURRING", "Recurring"


class BillingCadence(models.TextChoices):
    MONTHLY = "MONTHLY", "Monthly"
    QUARTERLY = "QUARTERLY", "Quarterly"
    BIANNUAL = "BIANNUAL", "Biannual"
    ONE_TIME = "ONE_TIME", "One time"


CADENCE_MONTHS = {
    BillingCadence.MONTHLY: 1,
    BillingCadence.QUARTERLY: 3,
    BillingCadence.BIANNUAL: 6,
    BillingCadence.ONE_TIME: 0,
}


class BillingDocumentType(models.TextChoices):
    BOLETA = "BOLETA", "Boleta"
    FACTURA = "FACTURA", "Factura"


INACTIVE_ORDER_STATUSES = (
    OrderStatus.DELIVERED,
    OrderStatus.CANCELLED,
    OrderStatus.REFUNDED,
)
UNPAID_ORDER_STATUSES = (OrderStatus.PENDING_PAYMENT, OrderStatus.PAYMENT_FAILED)
OPEN_SUBSCRIPTION_STATUSES = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAUSED,
    SubscriptionStatus.PAST_DUE,
)


class Package(BaseModel):
    program = models.ForeignKey(
        "assessments.Program", on_delete=models.PROTECT, related_name="packages"
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    supply_duration_weeks = models.PositiveSmallIntegerField(default=4)
    billing_type = models.CharField(
        max_length=12, choices=BillingType.choices, default=BillingType.ONE_TIME
    )
    billing_interval_months = models.PositiveSmallIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="PEN")
    is_active = models.BooleanField(default=True, db_index=True)
    position = models.PositiveIntegerField(default=0)
    # Part 5, 8.3 (B6).
    badge = models.CharField(max_length=30, blank=True, default="")
    billing_cadence = models.CharField(
        max_length=12, choices=BillingCadence.choices, blank=True, default=""
    )
    display_price_per_month = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    charge_per_cycle = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    included_services = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["position", "name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.billing_cadence:
            months = CADENCE_MONTHS[BillingCadence(self.billing_cadence)]
            if months:
                self.billing_type = BillingType.RECURRING
                self.billing_interval_months = months
            else:
                self.billing_type = BillingType.ONE_TIME
                self.billing_interval_months = None
        super().save(*args, **kwargs)

    @property
    def price(self) -> Decimal:
        if self.charge_per_cycle is not None:
            return self.charge_per_cycle
        return sum(
            (item.unit_price * item.quantity for item in self.items.all()),
            Decimal("0.00"),
        )

    @property
    def monthly_price(self) -> Decimal:
        if self.display_price_per_month is not None:
            return self.display_price_per_month
        months = self.billing_interval_months or 1
        return (self.price / months).quantize(Decimal("0.01"))


class PackageItem(BaseModel):
    package = models.ForeignKey(Package, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "treatments.Product", on_delete=models.PROTECT, related_name="package_items"
    )
    description = models.CharField(max_length=200, blank=True, default="")
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self) -> str:
        return f"{self.package_id}:{self.product_id}x{self.quantity}"


class OrderSequence(models.Model):
    """Single locked counter row for human-readable order numbers."""

    name = models.CharField(max_length=20, primary_key=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.name}:{self.last_number}"


class Subscription(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions"
    )
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="subscriptions")
    treatment = models.ForeignKey(
        "treatments.Treatment", on_delete=models.PROTECT, related_name="subscriptions"
    )
    status = models.CharField(
        max_length=12, choices=SubscriptionStatus.choices, default=SubscriptionStatus.ACTIVE
    )
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    next_billing_at = models.DateTimeField(null=True, blank=True, db_index=True)
    next_shipment_at = models.DateTimeField(null=True, blank=True)
    paused_until = models.DateField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = EncryptedTextField(blank=True, default="")
    cancellation_feedback = EncryptedTextField(blank=True, default="")
    pause_reason = EncryptedTextField(blank=True, default="")
    payment_method = models.ForeignKey(
        "payments.SavedPaymentMethod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    shipping_address = models.ForeignKey(
        "patients.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    needs_staff_review = models.BooleanField(default=False, db_index=True)
    staff_review_reason = models.CharField(max_length=200, blank=True, default="")
    renewal_failures = models.PositiveSmallIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    # Part 5, 8.3: price kept at start; renewals charge it (SUBSCRIPTION_PRICE_LOCK).
    charge_per_cycle = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "next_billing_at"])]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.package_id}:{self.status}"


class Order(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="orders"
    )
    treatment = models.ForeignKey(
        "treatments.Treatment", on_delete=models.PROTECT, related_name="orders"
    )
    package = models.ForeignKey(Package, on_delete=models.PROTECT, related_name="orders")
    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    payment_method = models.ForeignKey(
        "payments.SavedPaymentMethod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    number = models.CharField(max_length=32, unique=True)
    status = models.CharField(
        max_length=24,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING_PAYMENT,
        db_index=True,
    )
    placed_at = models.DateTimeField(db_index=True)
    currency = models.CharField(max_length=3, default="PEN")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    shipping = models.DecimalField(max_digits=10, decimal_places=2)
    tax = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_address = models.ForeignKey(
        "patients.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    shipping_address_snapshot = models.JSONField(default=dict)
    billing = EncryptedTextField(blank=True, default="", help_text="JSON billing details.")
    billing_document_type = models.CharField(max_length=10, blank=True, default="")
    billing_document_number = models.CharField(max_length=40, blank=True, default="")
    billing_document_key = models.CharField(max_length=255, blank=True, default="")
    notes = EncryptedTextField(blank=True, default="")
    tracking_carrier = models.CharField(max_length=80, blank=True, default="")
    tracking_number = models.CharField(max_length=80, blank=True, default="")
    tracking_url = models.URLField(max_length=500, blank=True, default="")
    estimated_delivery_date = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    refill_consumed = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = EncryptedTextField(blank=True, default="")

    class Meta:
        ordering = ["-placed_at"]
        indexes = [
            models.Index(fields=["user", "-placed_at"]),
            models.Index(fields=["treatment", "status"]),
        ]

    def __str__(self) -> str:
        return self.number


class OrderItem(BaseModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "treatments.Product", on_delete=models.PROTECT, related_name="order_items"
    )
    name = models.CharField(max_length=160)
    description = models.CharField(max_length=200, blank=True, default="")
    quantity = models.PositiveSmallIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    image_url = models.URLField(max_length=500, blank=True, default="")
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]

    def __str__(self) -> str:
        return f"{self.order_id}:{self.name}"


class OrderStatusHistory(models.Model):
    """Immutable history row for every order status change."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=24, blank=True, default="")
    to_status = models.CharField(max_length=24)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reason = EncryptedTextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.order_id}:{self.from_status}->{self.to_status}"


class OrderNote(BaseModel):
    """Internal, append-only staff note (Part 5, 7.1)."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="internal_notes")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    body = EncryptedTextField()

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"note:{self.order_id}"
