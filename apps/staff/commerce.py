"""Staff orders, subscriptions and refunds (contract Part 5, section 7)."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, QuerySet, Sum
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.enums import PaymentPurpose, PaymentStatus
from apps.assessments.models import Payment
from apps.assessments.payments import get_payment_provider
from apps.assessments.serializers import PaymentSerializer
from apps.assessments.services import money
from apps.catalog import runtime
from apps.clinical.evaluations import ROUTE_SHORT
from apps.core.audit import record_audit
from apps.core.exceptions import (
    Conflict,
    InvalidStateTransition,
    NoRefillsRemaining,
)
from apps.orders import services as order_services
from apps.orders.models import (
    Order,
    OrderItem,
    OrderNote,
    OrderStatus,
    Subscription,
    SubscriptionStatus,
)
from apps.orders.serializers import OrderSerializer, SubscriptionSerializer
from apps.payments.models import Refund, RefundStatus
from apps.staff.models import Permission
from apps.staff.permissions import MissingPermission, has_perm
from apps.treatments.models import TreatmentStatus

STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "PROCESSING": (
        OrderStatus.PRESCRIPTION_VERIFIED,
        OrderStatus.ON_HOLD,
        OrderStatus.COMPOUNDING,
    ),
    "IN_TRANSIT": (OrderStatus.IN_TRANSIT, OrderStatus.OUT_FOR_DELIVERY),
    "DELIVERED": (OrderStatus.DELIVERED,),
    "CLOSED": (
        OrderStatus.CANCELLED,
        OrderStatus.REFUNDED,
        OrderStatus.PAYMENT_FAILED,
        OrderStatus.PENDING_PAYMENT,
    ),
}
STAFF_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.PRESCRIPTION_VERIFIED: {OrderStatus.ON_HOLD, OrderStatus.COMPOUNDING},
    OrderStatus.ON_HOLD: {OrderStatus.PRESCRIPTION_VERIFIED},
    OrderStatus.COMPOUNDING: {OrderStatus.IN_TRANSIT},
    OrderStatus.IN_TRANSIT: {OrderStatus.OUT_FOR_DELIVERY},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED},
}
STAFF_CANCELLABLE = (
    OrderStatus.PENDING_PAYMENT,
    OrderStatus.PAYMENT_FAILED,
    OrderStatus.PRESCRIPTION_VERIFIED,
    OrderStatus.ON_HOLD,
    OrderStatus.COMPOUNDING,
)


def _bound(value: Any, *, end: bool) -> datetime:
    moment = datetime.combine(value + timedelta(days=1) if end else value, time.min)
    return timezone.make_aware(moment, timezone.get_current_timezone())


# ---------------------------------------------------------------------------
# Orders (7.1)
# ---------------------------------------------------------------------------


def orders_base() -> QuerySet[Order]:
    return Order.objects.select_related("user", "package", "treatment").prefetch_related("items")


def filtered_orders(params: dict[str, Any]) -> QuerySet[Order]:
    queryset = orders_base()
    if params.get("status_group"):
        queryset = queryset.filter(status__in=STATUS_GROUPS[params["status_group"]])
    if params.get("status"):
        queryset = queryset.filter(status=params["status"])
    if params.get("search"):
        term = params["search"]
        queryset = queryset.filter(Q(user__full_name__icontains=term) | Q(number__icontains=term))
    if params.get("from"):
        queryset = queryset.filter(placed_at__gte=_bound(params["from"], end=False))
    if params.get("to"):
        queryset = queryset.filter(placed_at__lt=_bound(params["to"], end=True))
    if params.get("program_id"):
        queryset = queryset.filter(package__program_id=params["program_id"])
    return queryset.order_by(params.get("ordering") or "-placed_at")


def order_summary() -> dict[str, int]:
    rows = Order.objects.values("status").annotate(n=Count("id"))
    by_status = {row["status"]: row["n"] for row in rows}
    return {
        "processing": sum(by_status.get(s, 0) for s in STATUS_GROUPS["PROCESSING"]),
        "in_transit": sum(by_status.get(s, 0) for s in STATUS_GROUPS["IN_TRANSIT"]),
        "delivered": by_status.get(OrderStatus.DELIVERED, 0),
    }


def dosage_label(order: Order) -> str:
    treatment = order.treatment
    parts = [
        part
        for part in (treatment.dose_label, ROUTE_SHORT.get(treatment.route, treatment.route))
        if part
    ]
    head = " ".join(parts)
    return f"{head} - {order.package.name}" if head else order.package.name


def order_row(order: Order) -> dict[str, Any]:
    first: OrderItem | None = next(iter(order.items.all()), None)
    return {
        "id": order.pk,
        "number": order.number,
        "placed_at": order.placed_at,
        "patient": {"id": order.user_id, "name": order.user.get_full_name()},
        "product_label": first.name if first else order.package.name,
        "dosage_label": dosage_label(order),
        "amount": money(order.total, order.currency),
        "status": order.status,
        "status_label": order_services.STATUS_LABELS[order.status],
        "subscription_id": order.subscription_id,
    }


def get_order(order_id: Any, *, lock: bool = False) -> Order:
    queryset = orders_base()
    if lock:
        queryset = Order.objects.select_for_update().select_related("user", "package", "treatment")
    order = queryset.filter(pk=order_id).first()
    if order is None:
        raise NotFound()
    return order


def order_detail(order: Order, *, viewer: User, request: Any) -> dict[str, Any]:
    body = dict(OrderSerializer(order).data)
    patient = order.user
    from apps.patients.services import get_or_create_profile

    body["patient"] = {
        "id": patient.pk,
        "name": patient.get_full_name(),
        "email": patient.email,
        "phone": get_or_create_profile(patient).phone or None,
    }
    body["treatment_id"] = order.treatment_id
    body["internal_notes"] = [
        {
            "id": note.pk,
            "body": note.body,
            "author_name": note.author.get_full_name() if note.author else None,
            "created_at": note.created_at,
        }
        for note in order.internal_notes.select_related("author")
    ]
    body["status_history"] = [
        {
            "from": entry.from_status or None,
            "to": entry.to_status,
            "at": entry.created_at,
            "actor_name": entry.actor.get_full_name() if entry.actor else None,
            "note": entry.reason or None,
        }
        for entry in order.status_history.select_related("actor").order_by("created_at")
    ]
    if has_perm(viewer, Permission.CLINICAL_READ):
        treatment = order.treatment
        body["prescription"] = {
            "medication_name": treatment.medication_name,
            "dosage_label": treatment.dose_label,
        }
    else:
        body["prescription"] = None
    record_audit(
        actor=viewer,
        action="order.viewed",
        object_type="Order",
        object_id=order.pk,
        request=request,
        patient_id=patient.pk,
    )
    return body


def transition(*, order_id: Any, data: dict[str, Any], viewer: User, request: Any) -> Order:
    to_status = data["to_status"]
    with transaction.atomic():
        order = get_order(order_id, lock=True)
        if to_status not in STAFF_TRANSITIONS.get(order.status, set()):
            raise InvalidStateTransition()
        if to_status == OrderStatus.IN_TRANSIT:
            tracking = data.get("tracking")
            if not tracking:
                raise ValidationError({"tracking": ["Tracking is required for IN_TRANSIT."]})
            _apply_tracking(order, tracking)
        order_services.transition(
            order, to_status, actor=viewer, reason=strip_tags(data.get("note") or "").strip()
        )
        record_audit(
            actor=viewer,
            action="order.status_changed",
            object_type="Order",
            object_id=order.pk,
            request=request,
            patient_id=order.user_id,
            changed_fields=["status"],
            metadata={"to": to_status},
        )
    return get_order(order.pk)


def _apply_tracking(order: Order, tracking: dict[str, Any]) -> None:
    order.tracking_carrier = tracking["carrier"].strip()
    order.tracking_number = tracking["tracking_number"].strip()
    order.tracking_url = tracking.get("tracking_url") or ""
    order.save(update_fields=["tracking_carrier", "tracking_number", "tracking_url", "updated_at"])


def update_tracking(*, order_id: Any, data: dict[str, Any], viewer: User, request: Any) -> Order:
    with transaction.atomic():
        order = get_order(order_id, lock=True)
        _apply_tracking(order, data)
        record_audit(
            actor=viewer,
            action="order.tracking_updated",
            object_type="Order",
            object_id=order.pk,
            request=request,
            changed_fields=["tracking"],
        )
    return get_order(order.pk)


def add_note(*, order_id: Any, body: str, viewer: User, request: Any) -> OrderNote:
    order = get_order(order_id)
    note = OrderNote.objects.create(order=order, author=viewer, body=strip_tags(body).strip())
    record_audit(
        actor=viewer,
        action="order.note_created",
        object_type="Order",
        object_id=order.pk,
        request=request,
    )
    return note


def cancel(*, order_id: Any, reason: str, viewer: User, request: Any) -> Order:
    from apps.support.tickets import create_refund_task

    with transaction.atomic():
        order = get_order(order_id, lock=True)
        if order.status not in STAFF_CANCELLABLE:
            raise InvalidStateTransition()
        paid = order.payments.filter(
            status__in=(PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED)
        ).exists()
        order_services.transition(
            order, OrderStatus.CANCELLED, actor=viewer, reason=strip_tags(reason).strip()
        )
        order_services._restore_refill(order)
        if paid:
            create_refund_task(
                title=f"Refund required for cancelled order {order.number}",
                order=order,
                patient=order.user,
            )
        record_audit(
            actor=viewer,
            action="order.cancelled",
            object_type="Order",
            object_id=order.pk,
            request=request,
            patient_id=order.user_id,
        )
    return get_order(order.pk)


# ---------------------------------------------------------------------------
# Subscriptions (7.2)
# ---------------------------------------------------------------------------


def subscriptions_base() -> QuerySet[Subscription]:
    return Subscription.objects.select_related("user", "package", "treatment")


def filtered_subscriptions(params: dict[str, Any]) -> QuerySet[Subscription]:
    queryset = subscriptions_base()
    if params.get("status"):
        queryset = queryset.filter(status=params["status"])
    if params.get("scope") == "REFILL_DUE":
        horizon = timezone.now() + timedelta(days=settings.REFILL_DUE_WINDOW_DAYS)
        queryset = queryset.filter(status=SubscriptionStatus.ACTIVE, next_billing_at__lte=horizon)
    if params.get("search"):
        term = params["search"]
        queryset = queryset.filter(
            Q(user__full_name__icontains=term) | Q(user__email__icontains=term)
        )
    if params.get("program_id"):
        queryset = queryset.filter(package__program_id=params["program_id"])
    return queryset.order_by("next_billing_at", "-created_at")


def subscription_summary() -> dict[str, int]:
    rows = Subscription.objects.values("status").annotate(n=Count("id"))
    by_status = {row["status"]: row["n"] for row in rows}
    return {
        "active": by_status.get(SubscriptionStatus.ACTIVE, 0),
        "paused": by_status.get(SubscriptionStatus.PAUSED, 0),
        "cancelled": by_status.get(SubscriptionStatus.CANCELLED, 0),
        "past_due": by_status.get(SubscriptionStatus.PAST_DUE, 0),
    }


def subscription_row(subscription: Subscription) -> dict[str, Any]:
    package = subscription.package
    return {
        "id": subscription.pk,
        "status": subscription.status,
        "patient": {"id": subscription.user_id, "name": subscription.user.get_full_name()},
        "protocol_name": subscription.treatment.title,
        "package_name": package.name,
        "price_per_month": money(package.monthly_price, package.currency),
        "next_refill_on": (
            timezone.localdate(subscription.next_shipment_at)
            if subscription.next_shipment_at
            else None
        ),
        "next_billing_at": subscription.next_billing_at,
    }


def get_subscription(subscription_id: Any) -> Subscription:
    subscription = subscriptions_base().filter(pk=subscription_id).first()
    if subscription is None:
        raise NotFound()
    return subscription


def subscription_detail(
    subscription: Subscription, *, viewer: User, request: Any
) -> dict[str, Any]:
    body = dict(SubscriptionSerializer(subscription).data)
    body["patient"] = {
        "id": subscription.user_id,
        "name": subscription.user.get_full_name(),
        "email": subscription.user.email,
    }
    body["orders"] = [order_row(order) for order in orders_base().filter(subscription=subscription)]
    body["payments"] = [
        dict(PaymentSerializer(payment).data)
        for payment in Payment.objects.filter(
            Q(subscription=subscription) | Q(order__subscription=subscription)
        )
        .distinct()
        .order_by("-created_at")
    ]
    body["history"] = [
        {"action": entry.action, "at": entry.created_at, "actor_id": entry.actor_id}
        for entry in _subscription_audit(subscription)
    ]
    record_audit(
        actor=viewer,
        action="subscription.viewed",
        object_type="Subscription",
        object_id=subscription.pk,
        request=request,
        patient_id=subscription.user_id,
    )
    return body


def _subscription_audit(subscription: Subscription) -> Any:
    from apps.core.models import AuditLog

    return (
        AuditLog.objects.filter(object_type="Subscription", object_id=str(subscription.pk))
        .exclude(action="subscription.viewed")
        .order_by("created_at")
    )


def subscription_action(
    *, subscription_id: Any, action: str, reason: str, viewer: User, request: Any
) -> Subscription:
    with transaction.atomic():
        subscription = Subscription.objects.select_for_update().get(
            pk=get_subscription(subscription_id).pk
        )
        if action == "pause":
            order_services.pause_subscription(
                subscription=subscription, resume_on=None, reason=reason, request=request
            )
        elif action == "resume":
            order_services.resume_subscription(subscription=subscription, request=request)
        else:
            order_services.cancel_subscription(
                subscription=subscription, reason=reason, feedback="", request=request
            )
    return get_subscription(subscription.pk)


def new_refill(*, subscription_id: Any, viewer: User, idempotency_key: str, request: Any) -> Order:
    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update(of=("self",))
            .select_related("package", "treatment", "shipping_address__ubigeo", "payment_method")
            .filter(pk=subscription_id)
            .first()
        )
        if subscription is None:
            raise NotFound()
        if subscription.status != SubscriptionStatus.ACTIVE:
            raise InvalidStateTransition()
        treatment = subscription.treatment
        today = timezone.localdate()
        expired = (
            treatment.prescription_expires_at is not None
            and treatment.prescription_expires_at < today
        )
        no_refills = treatment.has_refills and (treatment.refills_remaining or 0) <= 0
        if treatment.status != TreatmentStatus.ACTIVE or expired or no_refills:
            raise NoRefillsRemaining()
        method = subscription.payment_method
        if method is None or method.is_deleted:
            raise Conflict("The subscription has no saved payment method.")
        pricing = order_services.subscription_pricing(subscription)
        address = subscription.shipping_address
        order = Order.objects.create(
            user=subscription.user,
            treatment=treatment,
            package=subscription.package,
            subscription=subscription,
            payment_method=method,
            number=order_services._next_number(),
            status=OrderStatus.PENDING_PAYMENT,
            placed_at=timezone.now(),
            currency=pricing.currency,
            subtotal=pricing.subtotal,
            shipping=pricing.shipping,
            tax=pricing.tax,
            total=pricing.total,
            shipping_address=address,
            shipping_address_snapshot=order_services._snapshot(address) if address else {},
        )
        OrderItem.objects.bulk_create(
            [
                OrderItem(
                    order=order,
                    product=item.product,
                    name=item.product.name,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    image_url=item.product.image_url,
                    position=index,
                )
                for index, item in enumerate(subscription.package.items.select_related("product"))
            ]
        )
        order_services._history(
            order, "", OrderStatus.PENDING_PAYMENT, actor=viewer, reason="New refill by staff"
        )
        from apps.payments import services as payment_services

        payment_services.charge(
            user=subscription.user,
            purpose=PaymentPurpose.ORDER,
            amount=order.total,
            currency=order.currency,
            provider_token=method.provider_card_id,
            idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"refill:{idempotency_key}")),
            order=order,
            raise_on_decline=False,
        )
        record_audit(
            actor=viewer,
            action="subscription.refill_created",
            object_type="Subscription",
            object_id=subscription.pk,
            request=request,
            patient_id=subscription.user_id,
            metadata={"order_id": str(order.pk)},
        )
    return Order.objects.get(pk=order.pk)


# ---------------------------------------------------------------------------
# Refunds (7.3)
# ---------------------------------------------------------------------------


def refund(*, payment_id: Any, data: dict[str, Any], viewer: User, request: Any) -> Refund:
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(pk=payment_id).first()
        if payment is None:
            raise NotFound()
        if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
            raise InvalidStateTransition()
        pending = Refund.objects.filter(payment=payment, status=RefundStatus.PENDING).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0")
        remaining = payment.amount - payment.refunded_amount - pending
        requested = data.get("amount")
        if requested is not None:
            if requested["currency"] != payment.currency:
                raise ValidationError({"amount": ["Use the payment currency."]})
            amount = Decimal(str(requested["amount"]))
        else:
            amount = remaining
        if amount <= 0 or amount > remaining:
            raise ValidationError({"amount": [f"At most {remaining} {payment.currency}."]})
        limit, limit_currency = runtime.refund_approval_limit()
        # Fail closed: the limit cannot be compared across currencies, so a
        # refund in any other currency needs an admin (deviation 135).
        over_limit = limit_currency != payment.currency or amount > limit
        if viewer.role != Role.ADMIN and over_limit:
            raise MissingPermission(details={"required": "ADMIN"})
        refund_row = Refund.objects.create(
            payment=payment,
            amount=amount,
            currency=payment.currency,
            reason_code=data["reason_code"],
            note=strip_tags(data.get("note") or "").strip(),
            requested_by=viewer,
        )
        result = get_payment_provider().refund(
            provider_payment_id=payment.provider_payment_id,
            amount=amount,
            currency=payment.currency,
            idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"staff-refund:{refund_row.pk}")),
        )
        refund_row.provider_refund_id = result.provider_refund_id
        refund_row.save(update_fields=["provider_refund_id", "updated_at"])
        record_audit(
            actor=viewer,
            action="payment.refund_requested",
            object_type="Payment",
            object_id=payment.pk,
            request=request,
            patient_id=payment.user_id,
            metadata={"amount": str(amount), "reason_code": data["reason_code"]},
        )
    return refund_row
