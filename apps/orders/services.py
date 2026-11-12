"""Checkout, order state machine, refunds and subscriptions (contract Part 3, 6)."""

from __future__ import annotations

import calendar
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.services import ConsentService
from apps.assessments.enums import EvaluationStatus, PaymentPurpose, PaymentStatus
from apps.assessments.models import Evaluation, Payment
from apps.core.audit import record_audit
from apps.core.exceptions import (
    CancelNotAllowed,
    Conflict,
    ConsentRequired,
    InvalidStateTransition,
    NoRefillsRemaining,
    UnsupportedDeliveryArea,
)
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.orders.models import (
    OPEN_SUBSCRIPTION_STATUSES,
    UNPAID_ORDER_STATUSES,
    BillingDocumentType,
    BillingType,
    Order,
    OrderItem,
    OrderSequence,
    OrderStatus,
    OrderStatusHistory,
    Package,
    Subscription,
    SubscriptionStatus,
)
from apps.patients.models import Address
from apps.payments import services as payment_services
from apps.payments.models import SavedPaymentMethod
from apps.treatments.models import Treatment, TreatmentStatus

logger = structlog.get_logger(__name__)

CENT = Decimal("0.01")

STATUS_LABELS: dict[str, str] = {
    OrderStatus.PENDING_PAYMENT: "Pending Payment",
    OrderStatus.PAYMENT_FAILED: "Payment Failed",
    OrderStatus.PRESCRIPTION_VERIFIED: "Prescription Verified / Processing",
    OrderStatus.ON_HOLD: "On Hold",
    OrderStatus.COMPOUNDING: "Compounding (Pharmacy)",
    OrderStatus.IN_TRANSIT: "In Transit",
    OrderStatus.OUT_FOR_DELIVERY: "Out for Delivery",
    OrderStatus.DELIVERED: "Delivered",
    OrderStatus.CANCELLED: "Cancelled",
    OrderStatus.REFUNDED: "Refunded",
}

_REFUNDABLE = {
    OrderStatus.PRESCRIPTION_VERIFIED,
    OrderStatus.ON_HOLD,
    OrderStatus.COMPOUNDING,
    OrderStatus.IN_TRANSIT,
    OrderStatus.OUT_FOR_DELIVERY,
    OrderStatus.DELIVERED,
}

ORDER_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PENDING_PAYMENT: {
        OrderStatus.PRESCRIPTION_VERIFIED,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.PAYMENT_FAILED: {OrderStatus.PENDING_PAYMENT, OrderStatus.CANCELLED},
    OrderStatus.PRESCRIPTION_VERIFIED: {
        OrderStatus.ON_HOLD,
        OrderStatus.COMPOUNDING,
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
    },
    OrderStatus.ON_HOLD: {
        OrderStatus.PRESCRIPTION_VERIFIED,
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
    },
    OrderStatus.COMPOUNDING: {
        OrderStatus.IN_TRANSIT,
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
    },
    OrderStatus.IN_TRANSIT: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.REFUNDED},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}

PATIENT_CANCELLABLE = {
    OrderStatus.PENDING_PAYMENT,
    OrderStatus.PAYMENT_FAILED,
    OrderStatus.PRESCRIPTION_VERIFIED,
    OrderStatus.ON_HOLD,
}


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


# ---------------------------------------------------------------------------
# Pricing and validation
# ---------------------------------------------------------------------------


@dataclass
class Pricing:
    subtotal: Decimal
    shipping: Decimal
    tax: Decimal
    total: Decimal
    currency: str


def price_package(package: Package, *, locked_price: Decimal | None = None) -> Pricing:
    subtotal = _q(locked_price if locked_price is not None else package.price)
    shipping = _q(Decimal(str(settings.ORDER_SHIPPING_FEE)))
    tax = _q((subtotal + shipping) * Decimal(str(settings.ORDER_TAX_RATE)))
    return Pricing(
        subtotal=subtotal,
        shipping=shipping,
        tax=tax,
        total=_q(subtotal + shipping + tax),
        currency=package.currency,
    )


@dataclass
class Checkout:
    treatment: Treatment
    package: Package
    address: Address
    pricing: Pricing
    billing: dict[str, Any] | None
    notes: str
    items: list[dict[str, Any]] = field(default_factory=list)


def _validate_billing(billing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not billing:
        return None
    document_type = billing.get("document_type")
    number = str(billing.get("document_number") or "").strip()
    business_name = (billing.get("business_name") or "").strip() or None
    if document_type == BillingDocumentType.FACTURA:
        if not re.fullmatch(r"\d{11}", number):
            raise ValidationError({"billing.document_number": ["A RUC has 11 digits."]})
        if not business_name:
            raise ValidationError({"billing.business_name": ["Required for a factura."]})
    elif not re.fullmatch(r"[A-Za-z0-9]{8,12}", number):
        raise ValidationError({"billing.document_number": ["Enter 8 to 12 letters or digits."]})
    return {
        "document_type": document_type,
        "document_number": number,
        "business_name": business_name,
    }


def has_approved_evaluation(user: User, program_id: Any) -> bool:
    return Evaluation.objects.filter(
        assessment__user=user,
        assessment__program_id=program_id,
        status=EvaluationStatus.APPROVED,
    ).exists()


def build_checkout(user: User, data: dict[str, Any], *, lock: bool = False) -> Checkout:
    if not ConsentService.has_current_consents(user):
        raise ConsentRequired()
    treatments = Treatment.objects.select_related("program", "product")
    if lock:
        treatments = treatments.select_for_update(of=("self",))
    treatment = treatments.filter(pk=data["treatment_id"], patient=user).first()
    if treatment is None:
        raise ValidationError({"treatment_id": ["Unknown treatment."]})
    if treatment.status != TreatmentStatus.ACTIVE:
        raise ValidationError({"treatment_id": ["This treatment is not active."]})
    if (
        treatment.prescription_expires_at is not None
        and treatment.prescription_expires_at < timezone.localdate()
    ):
        raise ValidationError({"treatment_id": ["The prescription has expired."]})
    if treatment.has_refills and (treatment.refills_remaining or 0) <= 0:
        raise NoRefillsRemaining()
    package = (
        Package.objects.prefetch_related("items__product")
        .filter(pk=data["package_id"], is_active=True, program_id=treatment.program_id)
        .first()
    )
    if package is None:
        raise ValidationError({"package_id": ["Unknown package for this treatment."]})
    address = (
        Address.objects.select_related("ubigeo__parent__parent")
        .filter(pk=data["shipping_address_id"], user=user)
        .first()
    )
    if address is None:
        raise ValidationError({"shipping_address_id": ["Unknown address."]})
    if not address.is_deliverable:
        raise UnsupportedDeliveryArea()
    if Order.objects.filter(treatment=treatment, status__in=UNPAID_ORDER_STATUSES).exists():
        raise Conflict("An unpaid order already exists for this treatment.")
    items = [
        {
            "product": item.product,
            "product_id": str(item.product_id),
            "name": item.product.name,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": _q(item.unit_price),
            "image_url": item.product.image_url or None,
        }
        for item in package.items.all()
    ]
    return Checkout(
        treatment=treatment,
        package=package,
        address=address,
        pricing=price_package(package),
        billing=_validate_billing(data.get("billing")),
        notes=strip_tags(data.get("notes") or "").strip(),
        items=items,
    )


def _next_number() -> str:
    sequence, _created = OrderSequence.objects.get_or_create(name="ORDER")
    locked = OrderSequence.objects.select_for_update().get(pk=sequence.pk)
    locked.last_number += 1
    locked.save(update_fields=["last_number"])
    return f"{settings.ORDER_NUMBER_PREFIX}{locked.last_number:05d}"


def _snapshot(address: Address) -> dict[str, Any]:
    from apps.patients.serializers import address_snapshot

    return address_snapshot(address)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def transition(
    order: Order,
    to_status: str,
    *,
    actor: Any = None,
    reason: str = "",
    notify_patient: bool = True,
) -> Order:
    from_status = order.status
    if to_status not in ORDER_TRANSITIONS.get(from_status, set()):
        raise InvalidStateTransition()
    order.status = to_status
    fields = ["status", "updated_at"]
    now = timezone.now()
    if to_status == OrderStatus.DELIVERED:
        order.delivered_at = now
        fields.append("delivered_at")
    elif to_status == OrderStatus.CANCELLED:
        order.cancelled_at = now
        order.cancellation_reason = reason
        fields.extend(["cancelled_at", "cancellation_reason"])
    order.save(update_fields=fields)
    _history(order, from_status, to_status, actor=actor, reason=reason)
    if notify_patient:
        notify(
            order.user,
            NotificationType.ORDER_STATUS_CHANGED,
            target_kind="order",
            target_id=order.pk,
        )
    return order


def _history(order: Order, from_status: str, to_status: str, *, actor: Any, reason: str) -> None:
    OrderStatusHistory.objects.create(
        order=order,
        from_status=from_status,
        to_status=to_status,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        reason=reason,
    )


def _consume_refill(order: Order) -> bool:
    """Decrement a refill atomically. Returns False when none is left."""

    treatment = Treatment.objects.select_for_update().get(pk=order.treatment_id)
    if not treatment.has_refills:
        return True
    if (treatment.refills_remaining or 0) <= 0:
        return False
    treatment.refills_remaining = (treatment.refills_remaining or 0) - 1
    treatment.save(update_fields=["refills_remaining", "updated_at"])
    order.refill_consumed = True
    order.save(update_fields=["refill_consumed", "updated_at"])
    if treatment.refills_remaining == 0:
        notify(
            order.user, NotificationType.REFILL_LOW, target_kind="treatment", target_id=treatment.pk
        )
    return True


def _restore_refill(order: Order) -> None:
    if not order.refill_consumed:
        return
    treatment = Treatment.objects.select_for_update().get(pk=order.treatment_id)
    if treatment.has_refills:
        treatment.refills_remaining = min(
            (treatment.refills_remaining or 0) + 1, treatment.refills_total or 0
        )
        treatment.save(update_fields=["refills_remaining", "updated_at"])
    order.refill_consumed = False
    order.save(update_fields=["refill_consumed", "updated_at"])


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def _resolve_payment(
    user: User, payment: dict[str, Any], *, recurring: bool
) -> tuple[str, SavedPaymentMethod | None]:
    """Return (provider token to charge, saved method or None)."""

    method = payment.get("method")
    if method == "SAVED_METHOD":
        method_id = payment.get("payment_method_id")
        saved = (
            payment_services.active_methods(user).filter(pk=method_id).first()
            if method_id
            else None
        )
        if saved is None:
            raise ValidationError({"payment.payment_method_id": ["Unknown payment method."]})
        return saved.provider_card_id, saved
    token = str(payment.get("provider_token") or "").strip()
    if not token:
        raise ValidationError({"payment.provider_token": ["Required for CULQI."]})
    if payment.get("save_payment_method"):
        saved = payment_services.save_card(user=user, provider_token=token)
        return saved.provider_card_id, saved
    if recurring:
        raise ValidationError(
            {"payment.save_payment_method": ["A recurring package needs a saved card."]}
        )
    return token, None


def place_order(*, user: User, data: dict[str, Any], idempotency_key: str, request: Any) -> Order:
    with transaction.atomic():
        checkout = build_checkout(user, data, lock=True)
        recurring = checkout.package.billing_type == BillingType.RECURRING
        token, saved = _resolve_payment(user, data["payment"], recurring=recurring)
        pricing = checkout.pricing
        today = timezone.localdate()
        order = Order.objects.create(
            user=user,
            treatment=checkout.treatment,
            package=checkout.package,
            payment_method=saved,
            number=_next_number(),
            status=OrderStatus.PENDING_PAYMENT,
            placed_at=timezone.now(),
            currency=pricing.currency,
            subtotal=pricing.subtotal,
            shipping=pricing.shipping,
            tax=pricing.tax,
            total=pricing.total,
            shipping_address=checkout.address,
            shipping_address_snapshot=_snapshot(checkout.address),
            billing=json.dumps(checkout.billing) if checkout.billing else "",
            billing_document_type=(checkout.billing or {}).get("document_type") or "",
            notes=checkout.notes,
            estimated_delivery_date=today + timedelta(days=settings.ORDER_DELIVERY_DAYS_MAX),
        )
        OrderItem.objects.bulk_create(
            [
                OrderItem(
                    order=order,
                    product=item["product"],
                    name=item["name"],
                    description=item["description"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    image_url=item["image_url"] or "",
                    position=index,
                )
                for index, item in enumerate(checkout.items)
            ]
        )
        _history(order, "", OrderStatus.PENDING_PAYMENT, actor=user, reason="Checkout")
        payment_services.charge(
            user=user,
            purpose=PaymentPurpose.ORDER,
            amount=pricing.total,
            currency=pricing.currency,
            provider_token=token,
            idempotency_key=idempotency_key,
            order=order,
        )
        record_audit(
            actor=user,
            action="order.placed",
            object_type="Order",
            object_id=order.pk,
            request=request,
        )
    return order


def pay_order(
    *, order: Order, payment: dict[str, Any], idempotency_key: str, request: Any
) -> Payment:
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status not in UNPAID_ORDER_STATUSES:
            raise InvalidStateTransition()
        latest = locked.payments.order_by("-created_at").first()
        if latest is not None and latest.status == PaymentStatus.PENDING:
            raise Conflict("A payment for this order is still being processed.")
        recurring = locked.package.billing_type == BillingType.RECURRING
        token, saved = _resolve_payment(locked.user, payment, recurring=recurring)
        if saved is not None and locked.payment_method_id != saved.pk:
            locked.payment_method = saved
            locked.save(update_fields=["payment_method", "updated_at"])
        if locked.status == OrderStatus.PAYMENT_FAILED:
            transition(
                locked, OrderStatus.PENDING_PAYMENT, actor=request.user, notify_patient=False
            )
        result = payment_services.charge(
            user=locked.user,
            purpose=PaymentPurpose.ORDER,
            amount=locked.total,
            currency=locked.currency,
            provider_token=token,
            idempotency_key=idempotency_key,
            order=locked,
        )
        record_audit(
            actor=request.user,
            action="order.payment_retried",
            object_type="Order",
            object_id=locked.pk,
            request=request,
        )
    return result


def cancel_order(*, order: Order, reason: str, actor: Any, request: Any = None) -> Order:
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        if locked.status not in PATIENT_CANCELLABLE:
            raise CancelNotAllowed()
        paid = locked.payments.filter(
            status__in=(PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED)
        ).first()
        transition(
            locked,
            OrderStatus.CANCELLED,
            actor=actor,
            reason=strip_tags(reason or "").strip(),
        )
        _restore_refill(locked)
        if paid is not None:
            payment_services.request_refund(paid)
        record_audit(
            actor=actor,
            action="order.cancelled",
            object_type="Order",
            object_id=locked.pk,
            request=request,
        )
    return locked


# ---------------------------------------------------------------------------
# Payment webhook side effects (called inside the webhook transaction)
# ---------------------------------------------------------------------------


def on_order_payment_succeeded(payment: Payment) -> None:
    if payment.order_id is None:
        return
    order = Order.objects.select_for_update().select_related("package").get(pk=payment.order_id)
    if order.status == OrderStatus.CANCELLED:
        payment_services.request_refund(payment)
        return
    if order.status == OrderStatus.PAYMENT_FAILED:
        transition(order, OrderStatus.PENDING_PAYMENT, notify_patient=False)
    if order.status != OrderStatus.PENDING_PAYMENT:
        return
    if _consume_refill(order):
        transition(order, OrderStatus.PRESCRIPTION_VERIFIED, reason="Payment succeeded")
    else:
        transition(order, OrderStatus.PRESCRIPTION_VERIFIED, reason="Payment succeeded")
        transition(order, OrderStatus.ON_HOLD, reason="No refills remaining")
    if order.package.billing_type == BillingType.RECURRING and order.subscription_id is None:
        _start_subscription(order)


def on_order_payment_failed(payment: Payment) -> None:
    if payment.order_id is None:
        return
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    latest = order.payments.order_by("-created_at").first()
    if latest is not None and latest.pk != payment.pk:
        return
    if order.status == OrderStatus.PENDING_PAYMENT:
        transition(order, OrderStatus.PAYMENT_FAILED, reason="Payment failed")


def on_payment_refunded(payment: Payment, *, fully: bool) -> None:
    if payment.order_id is None or not fully:
        return
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if order.status in _REFUNDABLE:
        transition(order, OrderStatus.REFUNDED, reason="Refund succeeded")


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


def _start_subscription(order: Order) -> Subscription:
    package = order.package
    now = timezone.now()
    end = add_months(now, package.billing_interval_months or 1)
    subscription = Subscription.objects.create(
        user=order.user,
        package=package,
        treatment=order.treatment,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now,
        current_period_end=end,
        next_billing_at=end,
        next_shipment_at=end,
        payment_method=order.payment_method,
        shipping_address=order.shipping_address,
        charge_per_cycle=_q(package.price),
    )
    order.subscription = subscription
    order.save(update_fields=["subscription", "updated_at"])
    return subscription


def pause_subscription(
    *, subscription: Subscription, resume_on: date | None, reason: str, request: Any
) -> Subscription:
    if subscription.status != SubscriptionStatus.ACTIVE:
        raise InvalidStateTransition()
    today = timezone.localdate()
    if resume_on is not None and not (
        today < resume_on <= today + timedelta(days=settings.SUBSCRIPTION_PAUSE_MAX_DAYS)
    ):
        raise ValidationError(
            {"resume_on": [f"Must be within {settings.SUBSCRIPTION_PAUSE_MAX_DAYS} days."]}
        )
    subscription.status = SubscriptionStatus.PAUSED
    subscription.paused_until = resume_on
    subscription.next_billing_at = None
    subscription.pause_reason = strip_tags(reason or "").strip()
    subscription.save(
        update_fields=["status", "paused_until", "next_billing_at", "pause_reason", "updated_at"]
    )
    _audit_subscription(request, subscription, "subscription.paused")
    return subscription


def resume_subscription(*, subscription: Subscription, request: Any = None) -> Subscription:
    if subscription.status != SubscriptionStatus.PAUSED:
        raise InvalidStateTransition()
    now = timezone.now()
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.paused_until = None
    subscription.next_billing_at = (
        None if subscription.cancel_at_period_end else max(subscription.current_period_end, now)
    )
    subscription.save(update_fields=["status", "paused_until", "next_billing_at", "updated_at"])
    _audit_subscription(request, subscription, "subscription.resumed")
    return subscription


def cancel_subscription(
    *, subscription: Subscription, reason: str, feedback: str, request: Any
) -> Subscription:
    if subscription.status not in OPEN_SUBSCRIPTION_STATUSES or subscription.cancel_at_period_end:
        raise InvalidStateTransition()
    now = timezone.now()
    subscription.cancellation_reason = strip_tags(reason or "").strip()
    subscription.cancellation_feedback = strip_tags(feedback or "").strip()
    subscription.next_billing_at = None
    subscription.next_retry_at = None
    if subscription.current_period_end > now and subscription.status != SubscriptionStatus.PAST_DUE:
        subscription.cancel_at_period_end = True
    else:
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.cancelled_at = now
    subscription.save()
    _audit_subscription(request, subscription, "subscription.cancelled")
    return subscription


def _audit_subscription(request: Any, subscription: Subscription, action: str) -> None:
    record_audit(
        actor=getattr(request, "user", None),
        action=action,
        object_type="Subscription",
        object_id=subscription.pk,
        request=request,
    )


def subscription_pricing(subscription: Subscription) -> Pricing:
    locked = subscription.charge_per_cycle if settings.SUBSCRIPTION_PRICE_LOCK else None
    return price_package(subscription.package, locked_price=locked)


def charge_renewal(subscription: Subscription, *, now: datetime | None = None) -> Payment | None:
    pricing = subscription_pricing(subscription)
    method = subscription.payment_method
    period_key = subscription.current_period_end.isoformat()
    key = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"renewal:{subscription.pk}:{period_key}:{subscription.renewal_failures}",
        )
    )
    subscription.next_billing_at = None
    subscription.next_retry_at = None
    subscription.save(update_fields=["next_billing_at", "next_retry_at", "updated_at"])
    if method is None or method.is_deleted:
        _renewal_failed(subscription, now=now)
        return None
    payment = payment_services.charge(
        user=subscription.user,
        purpose=PaymentPurpose.SUBSCRIPTION_RENEWAL,
        amount=pricing.total,
        currency=pricing.currency,
        provider_token=method.provider_card_id,
        idempotency_key=key,
        subscription=subscription,
        raise_on_decline=False,
    )
    if payment.status == PaymentStatus.FAILED:
        _renewal_failed(subscription, now=now)
    return payment


def _renewal_failed(subscription: Subscription, *, now: datetime | None = None) -> None:
    now = now or timezone.now()
    retry_days = list(settings.SUBSCRIPTION_RETRY_DAYS)
    subscription.renewal_failures += 1
    subscription.status = SubscriptionStatus.PAST_DUE
    attempt = subscription.renewal_failures
    if attempt <= len(retry_days):
        previous = retry_days[attempt - 2] if attempt >= 2 else 0
        subscription.next_retry_at = now + timedelta(days=retry_days[attempt - 1] - previous)
    else:
        subscription.next_retry_at = None
    subscription.save(update_fields=["renewal_failures", "status", "next_retry_at", "updated_at"])
    notify(
        subscription.user,
        NotificationType.SUBSCRIPTION_PAYMENT_FAILED,
        target_kind="subscription",
        target_id=subscription.pk,
    )


def on_renewal_failed(payment: Payment) -> None:
    if payment.subscription_id is None:
        return
    subscription = Subscription.objects.select_for_update().get(pk=payment.subscription_id)
    _renewal_failed(subscription)


def on_renewal_succeeded(payment: Payment) -> None:
    if payment.subscription_id is None:
        return
    subscription = (
        Subscription.objects.select_for_update(of=("self",))
        .select_related("package", "treatment", "shipping_address__ubigeo")
        .get(pk=payment.subscription_id)
    )
    package = subscription.package
    pricing = subscription_pricing(subscription)
    now = timezone.now()
    address = subscription.shipping_address
    order = Order.objects.create(
        user=subscription.user,
        treatment=subscription.treatment,
        package=package,
        subscription=subscription,
        payment_method=subscription.payment_method,
        number=_next_number(),
        status=OrderStatus.PRESCRIPTION_VERIFIED,
        placed_at=now,
        currency=pricing.currency,
        subtotal=pricing.subtotal,
        shipping=pricing.shipping,
        tax=pricing.tax,
        total=pricing.total,
        shipping_address=address,
        shipping_address_snapshot=_snapshot(address) if address is not None else {},
        estimated_delivery_date=timezone.localdate()
        + timedelta(days=settings.ORDER_DELIVERY_DAYS_MAX),
    )
    OrderItem.objects.bulk_create(
        [
            OrderItem(
                order=order,
                product=item.product,
                name=item.product.name,
                description=item.description,
                quantity=item.quantity,
                unit_price=_q(item.unit_price),
                image_url=item.product.image_url,
                position=index,
            )
            for index, item in enumerate(package.items.select_related("product"))
        ]
    )
    _history(order, "", OrderStatus.PRESCRIPTION_VERIFIED, actor=None, reason="Renewal")
    if not _consume_refill(order):
        transition(order, OrderStatus.ON_HOLD, reason="No refills remaining")
    payment.order = order
    payment.save(update_fields=["order", "updated_at"])
    start = max(subscription.current_period_end, now)
    end = add_months(start, package.billing_interval_months or 1)
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.current_period_start = start
    subscription.current_period_end = end
    subscription.next_billing_at = None if subscription.cancel_at_period_end else end
    subscription.next_shipment_at = end
    subscription.renewal_failures = 0
    subscription.next_retry_at = None
    subscription.save()
    notify(
        subscription.user,
        NotificationType.SUBSCRIPTION_RENEWED,
        target_kind="subscription",
        target_id=subscription.pk,
    )


def run_subscription_jobs(now: datetime | None = None) -> dict[str, int]:
    now = now or timezone.now()
    today = timezone.localdate(now)
    counts = {"renewed": 0, "retried": 0, "cancelled": 0, "resumed": 0}

    for subscription in Subscription.objects.filter(
        cancel_at_period_end=True,
        status__in=OPEN_SUBSCRIPTION_STATUSES,
        current_period_end__lte=now,
    ):
        subscription.status = SubscriptionStatus.CANCELLED
        subscription.cancelled_at = now
        subscription.next_billing_at = None
        subscription.save(update_fields=["status", "cancelled_at", "next_billing_at", "updated_at"])
        counts["cancelled"] += 1

    for subscription in Subscription.objects.filter(
        status=SubscriptionStatus.PAUSED, paused_until__isnull=False, paused_until__lte=today
    ):
        resume_subscription(subscription=subscription)
        counts["resumed"] += 1

    due = Subscription.objects.filter(
        status=SubscriptionStatus.ACTIVE,
        cancel_at_period_end=False,
        next_billing_at__isnull=False,
        next_billing_at__lte=now,
    ).select_related("package", "payment_method")
    charged: set[Any] = set()
    for subscription in due:
        with transaction.atomic():
            charge_renewal(subscription, now=now)
        charged.add(subscription.pk)
        counts["renewed"] += 1

    retries = Subscription.objects.filter(
        status=SubscriptionStatus.PAST_DUE,
        cancel_at_period_end=False,
        next_retry_at__isnull=False,
        next_retry_at__lte=now,
    ).select_related("package", "payment_method")
    for subscription in retries:
        if subscription.pk in charged:
            continue
        with transaction.atomic():
            charge_renewal(subscription, now=now)
        counts["retried"] += 1
    return counts


__all__ = [
    "ORDER_TRANSITIONS",
    "STATUS_LABELS",
    "build_checkout",
    "cancel_order",
    "cancel_subscription",
    "charge_renewal",
    "has_approved_evaluation",
    "on_order_payment_failed",
    "on_order_payment_succeeded",
    "on_payment_refunded",
    "on_renewal_failed",
    "on_renewal_succeeded",
    "pause_subscription",
    "pay_order",
    "place_order",
    "price_package",
    "resume_subscription",
    "run_subscription_jobs",
    "transition",
]
