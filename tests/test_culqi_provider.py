"""Culqi provider adapter: request shapes and response mapping (HTTP layer mocked)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from apps.assessments.enums import PaymentStatus
from apps.assessments.payments import CardRejected, CulqiPaymentProvider, CustomerInfo
from apps.payments import culqi_api


class Recorder(list[tuple[str, str, Any]]):
    """Calls made to the mocked Culqi API, plus the canned answers by "METHOD path"."""

    responses: dict[str, Any]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorded = Recorder()
    responses: dict[str, Any] = {}
    recorded.responses = responses

    def fake_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        recorded.append((method, path, body))
        answer = responses.get(f"{method} {path.split('?')[0]}")
        if isinstance(answer, Exception):
            raise answer
        return dict(answer or {})

    monkeypatch.setattr(culqi_api, "request", fake_request)
    return recorded


def test_charge_approved_sends_cents_and_email(calls: Recorder) -> None:
    calls.responses["POST charges"] = {"id": "chr_test_1", "outcome": {"type": "venta_exitosa"}}
    result = CulqiPaymentProvider().create_payment(
        amount=Decimal("25.50"),
        currency="PEN",
        provider_token="tkn_test_1",
        idempotency_key="key-1",
        email="paciente@example.com",
    )
    assert result.status == PaymentStatus.SUCCEEDED
    assert result.provider_payment_id == "chr_test_1"
    _, path, body = calls[0]
    assert path == "charges"
    assert body["amount"] == 2550 and body["currency_code"] == "PEN"
    assert body["source_id"] == "tkn_test_1" and body["email"] == "paciente@example.com"


def test_charge_declined_maps_user_message(calls: Recorder) -> None:
    calls.responses["POST charges"] = culqi_api.CulqiError(
        400,
        {
            "object": "error",
            "type": "operacion_denegada",
            "charge_id": "chr_test_2",
            "code": "card_declined",
            "decline_code": "insufficient_funds",
            "user_message": "Su tarjeta no tiene fondos suficientes. " * 10,
        },
    )
    result = CulqiPaymentProvider().create_payment(
        amount=Decimal("10"), currency="PEN", provider_token="tkn", idempotency_key="k"
    )
    assert result.status == PaymentStatus.FAILED
    assert result.provider_payment_id == "chr_test_2"
    # Stored in Payment.failure_code (max 120): the short decline code, not the long message.
    assert result.failure_code == "insufficient_funds"


def test_verify_charge(calls: Recorder) -> None:
    calls.responses["GET charges/chr_ok"] = {"outcome": {"type": "venta_exitosa"}}
    calls.responses["GET charges/chr_no"] = {"outcome": {"type": "venta_denegada"}}
    provider = CulqiPaymentProvider()
    assert provider.verify_charge("chr_ok") is True
    assert provider.verify_charge("chr_no") is False
    assert provider.verify_charge("") is None


def test_create_card_reuses_customer_and_reads_expiry(calls: Recorder) -> None:
    calls.responses["GET customers"] = {"data": [{"id": "cus_test_1"}]}
    calls.responses["GET tokens/tkn_1"] = {"metadata": {"exp_month": "06", "exp_year": "2031"}}
    calls.responses["POST cards"] = {
        "id": "crd_test_1",
        "source": {"last_four": "1118", "iin": {"card_brand": "Mastercard"}},
    }
    card = CulqiPaymentProvider().create_card(
        provider_token="tkn_1",
        customer_ref="user-1",
        customer=CustomerInfo("a@example.com", "Ana", "Quispe", "987654321", "Av. 1", "Lima"),
    )
    assert (card.provider_card_id, card.brand, card.last4) == ("crd_test_1", "Mastercard", "1118")
    assert (card.exp_month, card.exp_year) == (6, 2031)
    assert not any(method == "POST" and path == "customers" for method, path, _ in calls)


def test_create_card_rejected(calls: Recorder) -> None:
    calls.responses["GET customers"] = {"data": [{"id": "cus_test_1"}]}
    calls.responses["GET tokens/tkn_bad"] = {}
    calls.responses["POST cards"] = culqi_api.CulqiError(400, {"user_message": "Tarjeta robada."})
    with pytest.raises(CardRejected):
        CulqiPaymentProvider().create_card(
            provider_token="tkn_bad",
            customer_ref="user-1",
            customer=CustomerInfo("a@example.com", "Ana", "Quispe", "", "", ""),
        )


def test_refund_sends_cents(calls: Recorder) -> None:
    calls.responses["POST refunds"] = {"id": "ref_test_1", "status": "completa"}
    result = CulqiPaymentProvider().refund(
        provider_payment_id="chr_test_1",
        amount=Decimal("599.00"),
        currency="PEN",
        idempotency_key="k",
    )
    assert result.provider_refund_id == "ref_test_1"
    assert calls[0][2] == {
        "amount": 59900,
        "charge_id": "chr_test_1",
        "reason": "solicitud_comprador",
    }
