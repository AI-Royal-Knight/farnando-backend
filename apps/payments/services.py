"""Charges, saved cards, refunds and webhook processing (contract Part 3, 6.7 and 7)."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.assessments.enums import FeeStatus, PaymentPurpose, PaymentStatus
from apps.assessments.models import Payment
from apps.assessments.payments import CardRejected, PaymentService, get_payment_provider
from apps.core.exceptions import PaymentFailed, PaymentMethodInUse
from apps.payments import culqi, daily
from apps.payments.models import SavedPaymentMethod, WebhookEvent, WebhookProvider

logger = structlog.get_logger(__name__)

FINAL_SUCCESS_STATUSES = (
    PaymentStatus.SUCCEEDED,
    PaymentStatus.REFUNDED,
    PaymentStatus.PARTIALLY_REFUNDED,
)


# ---------------------------------------------------------------------------
# Charges
# ---------------------------------------------------------------------------


def charge(
    *,
    user: User,
    purpose: str,
    amount: Decimal,
    currency: str,
    provider_token: str,
    idempotency_key: str,
    order: Any = None,
    subscription: Any = None,
    raise_on_decline: bool = True,
) -> Payment:
    """Create a provider charge. A synchronous success stays PENDING until the webhook."""

    result = get_payment_provider().create_payment(
        amount=amount,
        currency=currency,
        provider_token=provider_token,
        idempotency_key=idempotency_key,
    )
    status = PaymentStatus.PENDING if result.status == PaymentStatus.SUCCEEDED else result.status
    payment = Payment.objects.create(
        user=user,
        purpose=purpose,
        order=order,
        subscription=subscription,
        status=status,
        amount=amount,
        currency=currency,
        provider="CULQI",
        provider_payment_id=result.provider_payment_id,
        failure_code=result.failure_code or "",
        action=result.action,
    )
    if result.status == PaymentStatus.FAILED and raise_on_decline:
        raise PaymentFailed(result.failure_code or "The payment was declined.")
    schedule_auto_confirm(payment)
    return payment


def auto_confirm_enabled() -> bool:
    """Local/compose only: settle fake-provider charges without a provider webhook."""

    return bool(getattr(settings, "PAYMENTS_AUTO_CONFIRM", False)) and str(
        getattr(settings, "PAYMENT_PROVIDER", "")
    ).endswith("FakePaymentProvider")


def schedule_auto_confirm(payment: Payment) -> None:
    """After commit, apply the same transition a verified CHARGE_SUCCEEDED webhook would."""

    if not auto_confirm_enabled() or payment.status != PaymentStatus.PENDING:
        return
    payment_id = payment.pk

    def _confirm() -> None:
        with transaction.atomic():
            locked = Payment.objects.select_for_update().filter(pk=payment_id).first()
            if locked is not None and locked.status == PaymentStatus.PENDING:
                _charge_succeeded(locked)
        logger.info("payment_auto_confirmed", payment_id=str(payment_id))

    transaction.on_commit(_confirm)


def request_refund(payment: Payment) -> None:
    remaining = payment.amount - payment.refunded_amount
    if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED):
        return
    if remaining <= 0:
        return
    get_payment_provider().refund(
        provider_payment_id=payment.provider_payment_id,
        amount=remaining,
        currency=payment.currency,
        idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, f"refund:{payment.pk}")),
    )


# ---------------------------------------------------------------------------
# Saved cards
# ---------------------------------------------------------------------------


def active_methods(user: User) -> QuerySet[SavedPaymentMethod]:
    return SavedPaymentMethod.objects.filter(user=user, is_deleted=False)


def save_card(*, user: User, provider_token: str) -> SavedPaymentMethod:
    try:
        card = get_payment_provider().create_card(
            provider_token=provider_token, customer_ref=str(user.pk)
        )
    except CardRejected as exc:
        raise ValidationError({"provider_token": ["The card was rejected."]}) from exc
    with transaction.atomic():
        is_first = not active_methods(user).exists()
        method = SavedPaymentMethod.objects.create(
            user=user,
            provider="CULQI",
            provider_card_id=card.provider_card_id,
            brand=card.brand,
            last4=card.last4,
            exp_month=card.exp_month,
            exp_year=card.exp_year,
            is_default=False,
        )
        if is_first:
            set_default_method(user, method)
    return method


def set_default_method(user: User, method: SavedPaymentMethod) -> None:
    with transaction.atomic():
        active_methods(user).filter(is_default=True).exclude(pk=method.pk).update(is_default=False)
        if not method.is_default:
            method.is_default = True
            method.save(update_fields=["is_default", "updated_at"])


def delete_method(user: User, method: SavedPaymentMethod) -> None:
    from apps.orders.models import OPEN_SUBSCRIPTION_STATUSES, Subscription

    with transaction.atomic():
        others = active_methods(user).exclude(pk=method.pk).order_by("-is_default", "-created_at")
        replacement = others.first()
        subscriptions = Subscription.objects.filter(
            payment_method=method, status__in=OPEN_SUBSCRIPTION_STATUSES
        )
        if subscriptions.exists():
            if replacement is None:
                raise PaymentMethodInUse()
            subscriptions.update(payment_method=replacement, updated_at=timezone.now())
        was_default = method.is_default
        method.is_deleted = True
        method.is_default = False
        method.deleted_at = timezone.now()
        method.save(update_fields=["is_deleted", "is_default", "deleted_at", "updated_at"])
        if was_default and replacement is not None:
            set_default_method(user, replacement)


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class UnknownPayment(Exception):
    pass


def store_event(
    *, provider: str, event_id: str, event_type: str, payload: dict[str, Any]
) -> tuple[WebhookEvent, bool]:
    """Persist the raw event. Returns (event, created). Duplicates are not re-processed."""

    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(
                provider=provider,
                provider_event_id=event_id,
                event_type=event_type,
                payload=payload,
            )
            return event, True
    except IntegrityError:
        event = WebhookEvent.objects.get(provider=provider, provider_event_id=event_id)
        return event, False


def process_event(event: WebhookEvent) -> None:
    """Apply a stored event. Errors are recorded on the row, never raised."""

    try:
        with transaction.atomic():
            if event.provider == WebhookProvider.CULQI:
                _apply_culqi(culqi.parse(event.payload))
            elif event.provider == WebhookProvider.DAILY:
                _apply_daily(daily.parse(event.payload))
        event.processed_at = timezone.now()
        event.processing_error = ""
    except Exception as exc:
        logger.warning(
            "webhook_processing_failed",
            provider=event.provider,
            event_id=event.provider_event_id,
            error=type(exc).__name__,
        )
        event.processing_error = f"{type(exc).__name__}: {exc}"[:2000]
    event.save(update_fields=["processed_at", "processing_error"])


def parse_body(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected an object.")
    return payload


def _apply_culqi(parsed: culqi.ParsedEvent) -> None:
    if parsed.internal_event is None:
        return
    payment = (
        Payment.objects.select_for_update()
        .filter(provider_payment_id=parsed.charge_id)
        .exclude(provider_payment_id="")
        .first()
    )
    if payment is None:
        raise UnknownPayment(parsed.charge_id)
    if parsed.internal_event == culqi.CHARGE_SUCCEEDED:
        _charge_succeeded(payment)
    elif parsed.internal_event == culqi.CHARGE_FAILED:
        _charge_failed(payment, parsed.failure_code)
    elif parsed.internal_event == culqi.CHARGE_REQUIRES_ACTION:
        if payment.status == PaymentStatus.PENDING:
            payment.status = PaymentStatus.REQUIRES_ACTION
            payment.action = parsed.action or payment.action
            payment.save(update_fields=["status", "action", "updated_at"])
    elif parsed.internal_event == culqi.REFUND_SUCCEEDED:
        _refund_succeeded(payment, parsed.refunded_amount)


def _charge_succeeded(payment: Payment) -> None:
    if payment.status in FINAL_SUCCESS_STATUSES:
        return
    if payment.purpose == PaymentPurpose.EVALUATION_FEE:
        PaymentService.mark_succeeded(payment_id=payment.pk)
        return
    payment.status = PaymentStatus.SUCCEEDED
    payment.failure_code = ""
    payment.save(update_fields=["status", "failure_code", "updated_at"])
    from apps.orders import services as order_services

    if payment.purpose == PaymentPurpose.ORDER:
        order_services.on_order_payment_succeeded(payment)
    elif payment.purpose == PaymentPurpose.SUBSCRIPTION_RENEWAL:
        order_services.on_renewal_succeeded(payment)


def _charge_failed(payment: Payment, failure_code: str) -> None:
    # Out-of-order delivery: never move a successful payment backwards.
    if payment.status in FINAL_SUCCESS_STATUSES or payment.status == PaymentStatus.FAILED:
        return
    payment.status = PaymentStatus.FAILED
    payment.failure_code = failure_code or payment.failure_code
    payment.save(update_fields=["status", "failure_code", "updated_at"])
    from apps.orders import services as order_services

    if payment.purpose == PaymentPurpose.ORDER:
        order_services.on_order_payment_failed(payment)
    elif payment.purpose == PaymentPurpose.SUBSCRIPTION_RENEWAL:
        order_services.on_renewal_failed(payment)


def _refund_succeeded(payment: Payment, refunded: Decimal | None) -> None:
    if payment.status not in (
        PaymentStatus.SUCCEEDED,
        PaymentStatus.PARTIALLY_REFUNDED,
        PaymentStatus.REFUNDED,
    ):
        return
    total_refunded = refunded if refunded is not None else payment.amount
    total_refunded = min(max(total_refunded, payment.refunded_amount), payment.amount)
    _settle_refunds(payment, total_refunded - payment.refunded_amount)
    payment.refunded_amount = total_refunded
    fully = total_refunded >= payment.amount
    payment.status = PaymentStatus.REFUNDED if fully else PaymentStatus.PARTIALLY_REFUNDED
    payment.save(update_fields=["status", "refunded_amount", "updated_at"])
    if payment.purpose == PaymentPurpose.EVALUATION_FEE and fully and payment.fee is not None:
        payment.fee.status = FeeStatus.REFUNDED
        payment.fee.save(update_fields=["status", "updated_at"])
    elif payment.purpose in (PaymentPurpose.ORDER, PaymentPurpose.SUBSCRIPTION_RENEWAL):
        from apps.orders import services as order_services

        order_services.on_payment_refunded(payment, fully=fully)


def _settle_refunds(payment: Payment, newly_refunded: Decimal) -> None:
    """Mark pending staff refunds as succeeded, oldest first (Part 5, 7.3)."""

    from apps.payments.models import Refund, RefundStatus

    remaining = newly_refunded
    for refund in Refund.objects.filter(payment=payment, status=RefundStatus.PENDING).order_by(
        "created_at"
    ):
        if remaining < refund.amount:
            break
        refund.status = RefundStatus.SUCCEEDED
        refund.save(update_fields=["status", "updated_at"])
        remaining -= refund.amount


def _apply_daily(parsed: daily.ParsedEvent) -> None:
    if parsed.internal_event is None or not parsed.room_name:
        return
    from apps.consultations import services as consultation_services
    from apps.consultations.models import Consultation

    consultation = Consultation.objects.filter(video_room_name=parsed.room_name).first()
    if consultation is None:
        raise LookupError(parsed.room_name)
    if parsed.internal_event == daily.PARTICIPANT_JOINED:
        is_physician = parsed.user_id and parsed.user_id == str(consultation.physician_id)
        if is_physician and consultation.physician_joined_at is None:
            consultation.physician_joined_at = timezone.now()
            consultation.save(update_fields=["physician_joined_at", "updated_at"])
        consultation_services.participant_joined(consultation)
    elif parsed.internal_event == daily.MEETING_ENDED:
        consultation_services.meeting_ended(consultation)


__all__ = [
    "active_methods",
    "charge",
    "delete_method",
    "parse_body",
    "process_event",
    "request_refund",
    "save_card",
    "set_default_method",
    "store_event",
]
