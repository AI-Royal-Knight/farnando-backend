from __future__ import annotations

from django.contrib import admin
from django.db import models

from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatusHistory,
    Package,
    PackageItem,
    Subscription,
)


class PackageItemInline(admin.TabularInline):  # type: ignore[type-arg]
    model = PackageItem
    extra = 0
    raw_id_fields = ("product",)


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "program", "billing_type", "currency", "is_active", "position")
    list_filter = ("billing_type", "is_active", "program")
    inlines = [PackageItemInline]


class OrderItemInline(admin.TabularInline):  # type: ignore[type-arg]
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("product", "name", "description", "quantity", "unit_price")
    fields = readonly_fields


class OrderStatusHistoryInline(admin.TabularInline):  # type: ignore[type-arg]
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "actor", "created_at")
    fields = readonly_fields


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Status changes belong to the Part 5 staff API; the admin is read-mostly."""

    formfield_overrides = {models.URLField: {"assume_scheme": "https"}}
    list_display = ("number", "user", "status", "total", "currency", "placed_at")
    list_filter = ("status",)
    search_fields = ("number", "user__email")
    raw_id_fields = (
        "user",
        "treatment",
        "package",
        "subscription",
        "shipping_address",
        "payment_method",
    )
    readonly_fields = ("number", "status", "subtotal", "shipping", "tax", "total", "placed_at")
    exclude = ("billing", "notes", "cancellation_reason")
    inlines = [OrderItemInline, OrderStatusHistoryInline]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("user", "package", "status", "next_billing_at", "cancel_at_period_end")
    list_filter = ("status", "cancel_at_period_end")
    raw_id_fields = ("user", "package", "treatment", "payment_method", "shipping_address")
    exclude = ("cancellation_reason", "cancellation_feedback", "pause_reason")
