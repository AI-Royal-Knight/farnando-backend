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


@dataclass(frozen=True)
class CustomerInfo:
    """Who owns a stored card. Culqi requires these on its customer record."""

    email: str
    first_name: str
    last_name: str
    phone: str
    address: str
    city: str


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
        email: str = "",
    ) -> PaymentResult: ...

    def create_card(
        self, *, provider_token: str, customer_ref: str, customer: CustomerInfo | None = None
    ) -> CardResult: ...

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
        email: str = "",
    ) -> PaymentResult:
        _ = amount, currency, idempotency_key, email
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

    def create_card(
        self, *, provider_token: str, customer_ref: str, customer: CustomerInfo | None = None
    ) -> CardResult:
        _ = customer_ref, customer
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
    """Culqi API v2. The browser tokenizes the card; this charges, stores and refunds it.

    A synchronous approval is still recorded as PENDING (see ``PaymentService.create``);
    ``verify_charge`` lets the caller confirm it with Culqi when no webhook can reach us.
    """

    def create_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        provider_token: str,
        idempotency_key: str,
        email: str = "",
    ) -> PaymentResult:
        from apps.payments import culqi_api

        body = {
            "amount": culqi_api.to_cents(amount),
            "currency_code": currency,
            "email": email,
            "source_id": provider_token,
            # Culqi has no idempotency header; single-use tokens prevent a double charge.
            "metadata": {"idempotency_key": idempotency_key},
        }
        try:
            charge = culqi_api.request("POST", "charges", body)
        except culqi_api.CulqiError as exc:
            return PaymentResult(
                status=PaymentStatus.FAILED,
                provider_payment_id=str(exc.body.get("charge_id") or ""),
                failure_code=exc.code or "declined",
            )
        if charge.get("action_code") == "REVIEW":
            # 3-D Secure is required; the checkout does not run Culqi 3DS yet.
            return PaymentResult(
                status=PaymentStatus.FAILED,
                provider_payment_id=str(charge.get("id") or ""),
                failure_code="3ds_required",
            )
        approved = (charge.get("outcome") or {}).get("type") == culqi_api.APPROVED
        return PaymentResult(
            status=PaymentStatus.SUCCEEDED if approved else PaymentStatus.PENDING,
            provider_payment_id=str(charge.get("id") or ""),
        )

    def verify_charge(self, provider_payment_id: str) -> bool | None:
        """True when Culqi reports the charge approved, False when declined, None if unknown."""

        from apps.payments import culqi_api

        if not provider_payment_id:
            return None
        try:
            charge = culqi_api.request("GET", f"charges/{culqi_api.quote(provider_payment_id)}")
        except (culqi_api.CulqiError, ServiceUnavailable):
            return None
        outcome = (charge.get("outcome") or {}).get("type")
        if outcome == culqi_api.APPROVED:
            return True
        return False if outcome else None

    def create_card(
        self, *, provider_token: str, customer_ref: str, customer: CustomerInfo | None = None
    ) -> CardResult:
        from apps.payments import culqi_api

        if customer is None or not customer.email:
            raise CardRejected("A customer email is required to store a card.")
        try:
            customer_id = self._customer_id(customer)
            token = culqi_api.request("GET", f"tokens/{culqi_api.quote(provider_token)}")
            card = culqi_api.request(
                "POST",
                "cards",
                {
                    "customer_id": customer_id,
                    "token_id": provider_token,
                    "metadata": {"ref": customer_ref},
                },
            )
        except culqi_api.CulqiError as exc:
            raise CardRejected(exc.user_message or "The card was rejected.") from exc
        source = card.get("source") or {}
        expiry = token.get("metadata") or {}
        return CardResult(
            provider_card_id=str(card.get("id") or ""),
            brand=str((source.get("iin") or {}).get("card_brand") or "Card")[:30],
            last4=str(source.get("last_four") or "")[-4:],
            exp_month=_int(expiry.get("exp_month"), 12),
            exp_year=_int(expiry.get("exp_year"), 2099),
        )

    @staticmethod
    def _customer_id(customer: CustomerInfo) -> str:
        """Culqi allows one customer per email, so reuse it when it already exists."""

        from apps.payments import culqi_api

        found = culqi_api.request("GET", f"customers?email={culqi_api.quote(customer.email)}")
        for row in found.get("data") or []:
            if row.get("id"):
                return str(row["id"])
        created = culqi_api.request(
            "POST",
            "customers",
            {
                "first_name": customer.first_name[:50] or "Paciente",
                "last_name": customer.last_name[:50] or "Adelgaceya",
                "email": customer.email,
                "address": customer.address[:100] or "Lima",
                "address_city": customer.city[:30] or "Lima",
                "country_code": "PE",
                "phone_number": customer.phone or "999999999",
            },
        )
        return str(created["id"])

    def refund(
        self, *, provider_payment_id: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> RefundResult:
        from apps.payments import culqi_api

        _ = currency, idempotency_key
        try:
            refund = culqi_api.request(
                "POST",
                "refunds",
                {
                    "amount": culqi_api.to_cents(amount),
                    "charge_id": provider_payment_id,
                    "reason": "solicitud_comprador",
                },
            )
        except culqi_api.CulqiError as exc:
            raise ServiceUnavailable(exc.user_message or "The refund was not accepted.") from exc
        return RefundResult(provider_refund_id=str(refund.get("id") or ""), status="PENDING")


def _int(value: Any, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


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
            email=evaluation.assessment.user.email,
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
    def create_demo(*, evaluation: Evaluation, payment_method: str) -> Payment:
        """Settle the fee without contacting a provider. Local/demo only (see settings)."""

        fee = evaluation.fee
        if evaluation.status != EvaluationStatus.AWAITING_PAYMENT:
            if fee.status == FeeStatus.PAID:
                raise Conflict("This evaluation has already been paid.")
            raise InvalidStateTransition()
        if fee.status == FeeStatus.PAID:
            raise Conflict("This evaluation has already been paid.")

        payment = Payment.objects.create(
            user=evaluation.assessment.user,
            purpose=PaymentPurpose.EVALUATION_FEE,
            evaluation=evaluation,
            fee=fee,
            status=PaymentStatus.PENDING,
            amount=fee.amount,
            currency=fee.currency,
            provider=payment_method,
            provider_payment_id=f"demo_{uuid.uuid4().hex}",
            failure_code="",
        )
        return PaymentService.mark_succeeded(
            payment_id=payment.pk, reason="Demo payment (provider skipped)"
        )

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
