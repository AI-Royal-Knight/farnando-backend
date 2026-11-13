"""Payment-provider port and the Part 2 fee payment flow.

Part 3 owns the verified webhook. This module exposes ``mark_payment_succeeded``
for that webhook and deliberately does not treat a synchronous provider result
as final.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from django.conf import settings
from django.db import transaction

from apps.assessments.enums import EvaluationStatus, FeeStatus, PaymentPurpose, PaymentStatus
from apps.assessments.models import Evaluation, Payment
from apps.assessments.services import EvaluationService
from apps.core.exceptions import Conflict, InvalidStateTransition, PaymentFailed, ServiceUnavailable


@dataclass(frozen=True)
class PaymentResult:
    status: str
    provider_payment_id: str
    action: dict[str, str] | None = None
    failure_code: str | None = None


@dataclass(frozen=True)
class CardResult:
    provider_card_id: str
    brand: str
    last4: str
    exp_month: int
    exp_year: int


@dataclass(frozen=True)
class RefundResult:
    provider_refund_id: str
    status: str


class CardRejected(Exception):
    """The provider refused to store a card token."""


class PaymentProvider(Protocol):
    def create_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        provider_token: str,
        idempotency_key: str,
    ) -> PaymentResult: ...

    def create_card(self, *, provider_token: str, customer_ref: str) -> CardResult: ...

    def refund(
        self, *, provider_payment_id: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> RefundResult: ...


class FakePaymentProvider:
    """Deterministic local/test provider. Tokens starting with ``fail`` decline."""

    def create_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        provider_token: str,
        idempotency_key: str,
    ) -> PaymentResult:
        _ = amount, currency, idempotency_key
        if provider_token.lower().startswith("fail"):
            return PaymentResult(
                status=PaymentStatus.FAILED,
                provider_payment_id="",
                failure_code="The payment was declined.",
            )
        if provider_token.lower().startswith("3ds"):
            return PaymentResult(
                status=PaymentStatus.REQUIRES_ACTION,
                provider_payment_id=f"fake_{uuid.uuid4().hex}",
                action={"type": "REDIRECT", "url": "https://example.invalid/3ds"},
            )
        return PaymentResult(
            status=PaymentStatus.PENDING,
            provider_payment_id=f"fake_{uuid.uuid4().hex}",
        )

    def create_card(self, *, provider_token: str, customer_ref: str) -> CardResult:
        _ = customer_ref
        if provider_token.lower().startswith("fail"):
            raise CardRejected("The card was rejected.")
        return CardResult(
            provider_card_id=f"card_fake_{uuid.uuid4().hex}",
            brand="Visa",
            last4="4242",
            exp_month=12,
            exp_year=2030,
        )

    def refund(
        self, *, provider_payment_id: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> RefundResult:
        _ = provider_payment_id, amount, currency, idempotency_key
        return RefundResult(provider_refund_id=f"ref_fake_{uuid.uuid4().hex}", status="PENDING")


class CulqiPaymentProvider:
    """Production adapter placeholder until the Culqi contract is confirmed."""

    def create_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        provider_token: str,
        idempotency_key: str,
    ) -> PaymentResult:
        _ = amount, currency, provider_token, idempotency_key
        raise ServiceUnavailable("Culqi is not configured in this environment.")

    def create_card(self, *, provider_token: str, customer_ref: str) -> CardResult:
        _ = provider_token, customer_ref
        raise ServiceUnavailable("Culqi is not configured in this environment.")

    def refund(
        self, *, provider_payment_id: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> RefundResult:
        _ = provider_payment_id, amount, currency, idempotency_key
        raise ServiceUnavailable("Culqi is not configured in this environment.")


def get_payment_provider() -> PaymentProvider:
    path = str(getattr(settings, "PAYMENT_PROVIDER", ""))
    if path.endswith("FakePaymentProvider"):
        return FakePaymentProvider()
    if path.endswith("CulqiPaymentProvider"):
        return CulqiPaymentProvider()
    from django.utils.module_loading import import_string

    return import_string(path)()  # type: ignore[no-any-return]


class PaymentService:
    @staticmethod
    def create(
        *,
        evaluation: Evaluation,
        payment_method: str,
        provider_token: str,
        idempotency_key: str,
    ) -> Payment:
        fee = evaluation.fee
        if evaluation.status != EvaluationStatus.AWAITING_PAYMENT:
            if fee.status == FeeStatus.PAID:
                raise Conflict("This evaluation has already been paid.")
            raise InvalidStateTransition()
        if fee.status == FeeStatus.PAID:
            raise Conflict("This evaluation has already been paid.")

        result = get_payment_provider().create_payment(
            amount=fee.amount,
            currency=fee.currency,
            provider_token=provider_token,
            idempotency_key=idempotency_key,
        )
        status = result.status
        if status == PaymentStatus.SUCCEEDED:
            # A synchronous success is not final until the Part 3 webhook.
            status = PaymentStatus.PENDING
        payment = Payment.objects.create(
            user=evaluation.assessment.user,
            purpose=PaymentPurpose.EVALUATION_FEE,
            evaluation=evaluation,
            fee=fee,
            status=status,
            amount=fee.amount,
            currency=fee.currency,
            provider=payment_method,
            provider_payment_id=result.provider_payment_id,
            failure_code=result.failure_code or "",
            action=result.action,
        )
        if result.status == PaymentStatus.FAILED:
            raise PaymentFailed(result.failure_code or "The payment was declined.")
        from apps.payments.services import schedule_auto_confirm

        schedule_auto_confirm(payment)
        return payment

    @staticmethod
    def mark_succeeded(*, payment_id: Any, actor: Any = None, reason: str = "") -> Payment:
        """Called by the verified Part 3 webhook after signature validation."""

        with transaction.atomic():
            payment = (
                Payment.objects.select_for_update(of=("self",))
                .select_related("evaluation", "fee")
                .get(pk=payment_id)
            )
            if payment.status in (
                PaymentStatus.SUCCEEDED,
                PaymentStatus.REFUNDED,
                PaymentStatus.PARTIALLY_REFUNDED,
            ):
                return payment
            payment.status = PaymentStatus.SUCCEEDED
            payment.save(update_fields=["status", "updated_at"])
            fee = payment.fee
            if fee is not None:
                fee.status = FeeStatus.PAID
                fee.save(update_fields=["status", "updated_at"])
            evaluation = payment.evaluation
            if evaluation is not None and evaluation.status == EvaluationStatus.AWAITING_PAYMENT:
                EvaluationService.transition(
                    evaluation,
                    EvaluationStatus.SUBMITTED,
                    actor=actor,
                    reason=reason or "Payment succeeded",
                )
        return payment

    @staticmethod
    def mark_failed(*, payment_id: Any, failure_code: str = "") -> Payment:
        with transaction.atomic():
            payment = Payment.objects.select_for_update().get(pk=payment_id)
            if payment.status in (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED):
                raise InvalidStateTransition()
            payment.status = PaymentStatus.FAILED
            payment.failure_code = failure_code
            payment.save(update_fields=["status", "failure_code", "updated_at"])
        return payment


__all__ = [
    "CardRejected",
    "CardResult",
    "CulqiPaymentProvider",
    "FakePaymentProvider",
    "PaymentProvider",
    "PaymentResult",
    "PaymentService",
    "RefundResult",
    "get_payment_provider",
]
