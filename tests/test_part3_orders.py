"""Contract Part 3 sections 5-7: treatments, checkout, orders, subscriptions, payments."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Payment
from apps.core.models import AuditLog
from apps.notifications.models import Notification
from apps.orders import services as order_services
from apps.orders.models import Order, Subscription
from apps.payments.models import SavedPaymentMethod, WebhookEvent
from apps.treatments.models import Treatment
from apps.treatments.services import next_dose_due
from tests.factories.part3 import (
    culqi_event,
    culqi_post,
    give_consents,
    idem,
    make_address,
    make_evaluation,
    make_physician,
    make_treatment,
    package,
    program,
)
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def physician(db: Any) -> User:
    return make_physician()


@pytest.fixture
def treatment(patient_user: User, physician: User) -> Treatment:
    give_consents(patient_user)
    _, evaluation = make_evaluation(patient_user, "APPROVED", physician=physician)
    return make_treatment(patient_user, physician, evaluation=evaluation)


@pytest.fixture
def address(patient_user: User) -> Any:
    return make_address(patient_user)


def checkout_body(treatment: Treatment, address: Any, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "treatment_id": str(treatment.pk),
        "package_id": str(package().pk),
        "shipping_address_id": str(address.pk),
        "payment": {"method": "CULQI", "provider_token": "tkn_ok"},
        "notes": "Dejar en conserjería",
    }
    body.update(overrides)
    return body


def place(client: APIClient, body: dict[str, Any]) -> Any:
    return client.post("/api/v1/orders/", body, format="json", HTTP_IDEMPOTENCY_KEY=idem())


def charge_id(order_id: str) -> str:
    return Payment.objects.filter(order_id=order_id).latest("created_at").provider_payment_id


# --- Treatments -----------------------------------------------------------------


def test_treatment_list_and_detail(auth_client: APIClient, treatment: Treatment) -> None:
    make_treatment(
        treatment.patient, treatment.prescribing_physician, status="COMPLETED", refills=None
    )
    listing = auth_client.get("/api/v1/treatments/").json()
    assert listing["count"] == 1
    item = listing["results"][0]
    assert item["medication_name"] == "Semaglutide GLP-1"
    assert item["refills"] == {"remaining": 2, "total": 2}
    assert item["next_dose"]["time_of_day"] == "MORNING"
    assert item["next_dose"]["due_at"].endswith("13:00:00Z")
    assert item["prescribing_physician"]["name"] == "Dr. Marcus Vance, MD"
    assert auth_client.get("/api/v1/treatments/?status=ACTIVE,COMPLETED").json()["count"] == 2
    assert auth_client.get("/api/v1/treatments/?status=NOPE").status_code == 400

    detail = auth_client.get(f"/api/v1/treatments/{treatment.pk}/").json()
    assert detail["title"] == "Semaglutide 0.5mg"
    assert detail["schedule"] == {"frequency": "WEEKLY", "day_of_week": 2, "time_of_day": "MORNING"}
    assert detail["current_dose"] == {"value": 0.5, "unit": "mg", "label": "0.5 mg"}
    assert [item["title"] for item in detail["lifestyle_essentials"]] == [
        "High Protein",
        "Hydration",
        "Movement",
    ]
    assert detail["safety_information_markdown"].startswith("**[MEDICAL REVIEW]**")
    assert detail["prescription_pdf_available"] is False
    assert detail["last_order_id"] is None
    assert detail["next_check_in"] is None


def test_next_dose_schedules(patient_user: User, physician: User) -> None:
    now = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)  # Wednesday 10:00 Lima
    start = date(2026, 11, 1)
    cases = {
        ("DAILY", "MORNING"): datetime(2026, 11, 12, 13, 0, tzinfo=UTC),
        ("DAILY", "EVENING"): datetime(2026, 11, 12, 1, 0, tzinfo=UTC),
        ("WEEKLY", "MORNING"): datetime(2026, 11, 17, 13, 0, tzinfo=UTC),
        ("EVERY_TWO_WEEKS", "ANY"): datetime(2026, 11, 15, 14, 0, tzinfo=UTC),
        ("MONTHLY", "AFTERNOON"): datetime(2026, 12, 1, 19, 0, tzinfo=UTC),
    }
    for (frequency, time_of_day), expected in cases.items():
        treatment = make_treatment(
            patient_user,
            physician,
            frequency=frequency,
            time_of_day=time_of_day,
            started_at=start,
        )
        assert next_dose_due(treatment, now=now, tz_name="America/Lima") == expected, frequency
    as_needed = make_treatment(patient_user, physician, frequency="AS_NEEDED")
    assert next_dose_due(as_needed, now=now) is None
    paused = make_treatment(patient_user, physician, status="PAUSED")
    assert next_dose_due(paused, now=now) is None


def test_prescription_download(auth_client: APIClient, treatment: Treatment) -> None:
    missing = auth_client.get(f"/api/v1/treatments/{treatment.pk}/prescription/download/")
    assert missing.status_code == 409
    key = default_storage.save("prescriptions/test.pdf", ContentFile(b"%PDF-1.4"))
    treatment.prescription_pdf_key = key
    treatment.save()
    response = auth_client.get(f"/api/v1/treatments/{treatment.pk}/prescription/download/")
    assert response.status_code == 200
    assert set(response.json()) == {"url", "expires_at"}
    assert AuditLog.objects.filter(action="prescription.downloaded").exists()


def test_renewal_request(auth_client: APIClient, treatment: Treatment) -> None:
    url = f"/api/v1/treatments/{treatment.pk}/renewal-requests/"
    response = auth_client.post(url, {"note": "Need more"}, format="json")
    assert response.status_code == 201
    assert response.json()["status"] == "REQUESTED"
    assert auth_client.post(url, {}, format="json").json()["code"] == "conflict"
    treatment.status = "PAUSED"
    treatment.save()
    other = make_treatment(treatment.patient, treatment.prescribing_physician, status="PAUSED")
    assert (
        auth_client.post(f"/api/v1/treatments/{other.pk}/renewal-requests/").json()["code"]
        == "invalid_state_transition"
    )


def test_treatment_scoping(
    auth_client: APIClient, admin_client: APIClient, staff_client: APIClient, physician: User
) -> None:
    other = make_treatment(PatientFactory(), physician)
    assert auth_client.get(f"/api/v1/treatments/{other.pk}/").status_code == 404
    assert admin_client.get(f"/api/v1/treatments/{other.pk}/").status_code == 200
    assert admin_client.get("/api/v1/treatments/").json()["count"] == 1
    assert AuditLog.objects.filter(action="treatment.read").exists()
    assert staff_client.get("/api/v1/treatments/").status_code == 403


# --- Packages and quote ---------------------------------------------------------


def test_packages_require_approved_evaluation(
    auth_client: APIClient, patient_user: User, physician: User
) -> None:
    url = f"/api/v1/programs/{program().pk}/packages/"
    assert auth_client.get(url).json()["code"] == "evaluation_required"
    make_evaluation(patient_user, "APPROVED", physician=physician)
    body = auth_client.get(url).json()
    assert [item["name"] for item in body] == [
        "Monthly Protocol",
        "Custom 3-Month Protocol",
        "Monthly Subscription",
    ]
    three_month = body[1]
    assert three_month["supply_duration_weeks"] == 12
    assert three_month["price"] == {"amount": "1599.00", "currency": "PEN"}
    assert three_month["items"][0]["description"] == "2mL vial + needles"
    assert body[2]["billing_type"] == "RECURRING" and body[2]["billing_interval_months"] == 1


@override_settings(ORDER_SHIPPING_FEE="15.00", ORDER_TAX_RATE="0.18")
def test_quote(auth_client: APIClient, treatment: Treatment, address: Any) -> None:
    body = checkout_body(treatment, address)
    body.pop("payment")
    response = auth_client.post("/api/v1/orders/quote/", body, format="json")
    assert response.status_code == 200, response.json()
    quote = response.json()
    assert quote["subtotal"] == {"amount": "1599.00", "currency": "PEN"}
    assert quote["shipping"]["amount"] == "15.00"
    assert quote["tax"]["amount"] == "290.52"
    assert quote["total"]["amount"] == "1904.52"
    assert quote["estimated_delivery_days"] == {"min": 2, "max": 5}
    assert Order.objects.count() == 0


def test_checkout_validation(
    auth_client: APIClient, patient_user: User, treatment: Treatment, address: Any
) -> None:
    arequipa = make_address(patient_user, code="040101")
    response = place(auth_client, checkout_body(treatment, arequipa))
    assert response.status_code == 400
    assert response.json()["code"] == "unsupported_delivery_area"

    foreign = make_address(PatientFactory())
    assert place(auth_client, checkout_body(treatment, foreign)).status_code == 400

    treatment.refills_remaining = 0
    treatment.save()
    empty = place(auth_client, checkout_body(treatment, address))
    assert empty.status_code == 409
    assert empty.json()["code"] == "no_refills_remaining"
    treatment.refills_remaining = 2
    treatment.prescription_expires_at = timezone.localdate() - timedelta(days=1)
    treatment.save()
    assert place(auth_client, checkout_body(treatment, address)).status_code == 400
    treatment.prescription_expires_at = None
    treatment.save()

    recurring = checkout_body(
        treatment, address, package_id=str(package("Monthly Subscription").pk)
    )
    assert place(auth_client, recurring).status_code == 400
    factura = checkout_body(
        treatment,
        address,
        billing={"document_type": "FACTURA", "document_number": "123", "business_name": None},
    )
    assert place(auth_client, factura).status_code == 400
    no_token = checkout_body(treatment, address, payment={"method": "CULQI"})
    assert place(auth_client, no_token).status_code == 400

    declined = place(
        auth_client,
        checkout_body(treatment, address, payment={"method": "CULQI", "provider_token": "fail"}),
    )
    assert declined.status_code == 402
    assert declined.json()["code"] == "payment_failed"
    assert Order.objects.count() == 0


def test_checkout_requires_consent(patient_user: User, physician: User, address: Any) -> None:
    _, evaluation = make_evaluation(patient_user, "APPROVED", physician=physician)
    treatment = make_treatment(patient_user, physician, evaluation=evaluation)
    from rest_framework_simplejwt.tokens import RefreshToken

    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(patient_user).access_token}"
    )
    response = place(client, checkout_body(treatment, address))
    assert response.status_code == 403
    assert response.json()["code"] == "consent_required"


def test_order_lifecycle_with_webhooks(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> None:
    body = checkout_body(
        treatment,
        address,
        billing={"document_type": "BOLETA", "document_number": "45678912"},
    )
    response = place(auth_client, body)
    assert response.status_code == 201, response.json()
    order = response.json()
    assert order["number"] == "ADL-ORD-00001"
    assert order["status"] == "PENDING_PAYMENT"
    assert order["status_label"] == "Pending Payment"
    assert order["payment"]["status"] == "PENDING"
    assert order["payment"]["purpose"] == "ORDER"
    assert order["payment"]["refunded_amount"] == {"amount": "0.00", "currency": "PEN"}
    assert order["total"] == {"amount": "1599.00", "currency": "PEN"}
    assert order["shipping_address"]["district"] == "Miraflores"
    assert order["can_cancel"] is True
    assert order["items"][0]["unit_price"]["amount"] == "1599.00"

    second = place(auth_client, checkout_body(treatment, address))
    assert second.status_code == 409 and second.json()["code"] == "conflict"

    payment_id = order["payment"]["id"]
    polled = auth_client.get(f"/api/v1/payments/{payment_id}/")
    assert polled.status_code == 200 and polled.json()["status"] == "PENDING"

    success = culqi_event("charge.creation.succeeded", charge_id(order["id"]))
    assert culqi_post(api_client, success).status_code == 200
    detail = auth_client.get(f"/api/v1/orders/{order['id']}/").json()
    assert detail["status"] == "PRESCRIPTION_VERIFIED"
    assert detail["status_label"] == "Prescription Verified / Processing"
    assert [row["status"] for row in detail["timeline"]] == [
        "PENDING_PAYMENT",
        "PRESCRIPTION_VERIFIED",
    ]
    treatment.refresh_from_db()
    assert treatment.refills_remaining == 1
    assert Notification.objects.filter(type="ORDER_STATUS_CHANGED").count() == 1

    # Replay is ignored; an out-of-order failure never moves success backwards.
    assert culqi_post(api_client, success).status_code == 200
    late_fail = culqi_event("charge.creation.failed", charge_id(order["id"]))
    culqi_post(api_client, late_fail)
    assert Payment.objects.get(pk=payment_id).status == "SUCCEEDED"
    treatment.refresh_from_db()
    assert treatment.refills_remaining == 1

    # Staff moves it forward (Part 5), the patient can no longer cancel.
    db_order = Order.objects.get(pk=order["id"])
    order_services.transition(db_order, "COMPOUNDING")
    cancel = auth_client.post(f"/api/v1/orders/{order['id']}/cancel/", {}, format="json")
    assert cancel.status_code == 409 and cancel.json()["code"] == "cancel_not_allowed"
    order_services.transition(db_order, "IN_TRANSIT")
    order_services.transition(db_order, "OUT_FOR_DELIVERY")
    order_services.transition(db_order, "DELIVERED")

    listing = auth_client.get("/api/v1/orders/").json()
    assert listing["summary"] == {"total_count": 1, "active_count": 0, "delivered_count": 1}
    assert listing["results"][0]["delivered_at"] is not None
    assert auth_client.get("/api/v1/orders/?status_group=ACTIVE").json()["count"] == 0
    assert auth_client.get("/api/v1/orders/?status_group=DELIVERED").json()["count"] == 1
    assert auth_client.get("/api/v1/orders/?status_group=NOPE").status_code == 400

    refund = culqi_event(
        "refund.creation.succeeded",
        "",
        charge_id=charge_id(order["id"]),
        amount_refunded=159900,
    )
    culqi_post(api_client, refund)
    db_order.refresh_from_db()
    assert db_order.status == "REFUNDED"
    payment = Payment.objects.get(pk=payment_id)
    assert payment.status == "REFUNDED" and payment.refunded_amount == Decimal("1599.00")
    assert json.loads(db_order.billing)["document_number"] == "45678912"


def test_payment_failure_retry_and_cancel_refund(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> None:
    order = place(auth_client, checkout_body(treatment, address)).json()
    first_charge = charge_id(order["id"])
    pending_retry = auth_client.post(
        f"/api/v1/orders/{order['id']}/pay/",
        {"method": "CULQI", "provider_token": "tkn_2"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert pending_retry.status_code == 409

    culqi_post(
        api_client,
        culqi_event("charge.creation.failed", first_charge, outcome={"code": "card_declined"}),
    )
    detail = auth_client.get(f"/api/v1/orders/{order['id']}/").json()
    assert detail["status"] == "PAYMENT_FAILED"
    assert detail["payment"]["failure_code"] == "card_declined"

    retry = auth_client.post(
        f"/api/v1/orders/{order['id']}/pay/",
        {"method": "CULQI", "provider_token": "tkn_2"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert retry.status_code == 200, retry.json()
    assert retry.json()["status"] == "PENDING"
    assert Order.objects.get(pk=order["id"]).status == "PENDING_PAYMENT"
    culqi_post(api_client, culqi_event("charge.succeeded", charge_id(order["id"])))
    treatment.refresh_from_db()
    assert treatment.refills_remaining == 1

    cancelled = auth_client.post(
        f"/api/v1/orders/{order['id']}/cancel/", {"reason": "Changed my mind"}, format="json"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    treatment.refresh_from_db()
    assert treatment.refills_remaining == 2
    culqi_post(
        api_client,
        culqi_event(
            "refund.succeeded", "", charge_id=charge_id(order["id"]), amount_refunded=50000
        ),
    )
    payment = Payment.objects.filter(order_id=order["id"]).latest("created_at")
    assert payment.status == "PARTIALLY_REFUNDED"
    assert Order.objects.get(pk=order["id"]).status == "CANCELLED"


def test_requires_action_and_unknown_events(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> None:
    order = place(auth_client, checkout_body(treatment, address)).json()
    action = culqi_event(
        "charge.requires_action", charge_id(order["id"]), action_url="https://3ds.example/x"
    )
    culqi_post(api_client, action)
    payment = Payment.objects.get(pk=order["payment"]["id"])
    assert payment.status == "REQUIRES_ACTION"
    assert payment.action == {"type": "REDIRECT", "url": "https://3ds.example/x"}
    culqi_post(api_client, culqi_event("customer.created", "x"))
    culqi_post(api_client, culqi_event("charge.succeeded", "ch_missing"))
    event = WebhookEvent.objects.get(payload__data__id="ch_missing")
    assert "UnknownPayment" in event.processing_error


def test_culqi_webhook_security(api_client: APIClient) -> None:
    payload = culqi_event("charge.succeeded", "x")
    bad = culqi_post(api_client, payload, secret="wrong")
    assert bad.status_code == 401
    unsigned = api_client.post("/api/v1/webhooks/culqi/", payload, format="json")
    assert unsigned.status_code == 401
    assert WebhookEvent.objects.count() == 0
    with override_settings(CULQI_WEBHOOK_SECRET=""):
        assert culqi_post(api_client, payload, secret="").status_code == 401
    with override_settings(WEBHOOK_MAX_BODY_BYTES=10):
        too_big = culqi_post(api_client, payload)
        assert too_big.status_code == 413
        assert too_big.json()["code"] == "payload_too_large"
    malformed = culqi_post(api_client, {"type": "charge.succeeded"})
    assert malformed.status_code == 400
    string_data = {"id": "evt_s", "type": "charge.succeeded", "data": json.dumps({"id": "zz"})}
    assert culqi_post(api_client, string_data).status_code == 200


def test_evaluation_fee_webhook(
    auth_client: APIClient, api_client: APIClient, patient_user: User
) -> None:
    assessment, evaluation = make_evaluation(patient_user, "AWAITING_PAYMENT")
    response = auth_client.post(
        f"/api/v1/assessments/{assessment.pk}/fee/pay/",
        {"payment_method": "CULQI", "provider_token": "tkn"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["purpose"] == "EVALUATION_FEE"
    payment = Payment.objects.get(pk=response.json()["id"])
    culqi_post(api_client, culqi_event("charge.succeeded", payment.provider_payment_id))
    assert evaluation is not None
    evaluation.refresh_from_db()
    assert evaluation.status == "SUBMITTED"
    assert evaluation.fee.status == "PAID"
    assert Notification.objects.filter(user=patient_user, type="EVALUATION_STATUS_CHANGED").exists()
    culqi_post(
        api_client,
        culqi_event("refund.succeeded", "", charge_id=payment.provider_payment_id),
    )
    evaluation.fee.refresh_from_db()
    assert evaluation.fee.status == "REFUNDED"


def test_payment_scoping(
    auth_client: APIClient, admin_client: APIClient, treatment: Treatment, address: Any
) -> None:
    order = place(auth_client, checkout_body(treatment, address)).json()
    other = make_auth_client_for(PatientFactory())
    assert other.get(f"/api/v1/payments/{order['payment']['id']}/").status_code == 404
    assert other.get(f"/api/v1/orders/{order['id']}/").status_code == 404
    assert admin_client.get(f"/api/v1/payments/{order['payment']['id']}/").status_code == 200
    assert admin_client.get(f"/api/v1/orders/{order['id']}/").status_code == 200
    assert admin_client.get("/api/v1/orders/").json()["count"] == 1
    assert AuditLog.objects.filter(action="payment.read").exists()


def make_auth_client_for(user: User) -> APIClient:
    from rest_framework_simplejwt.tokens import RefreshToken

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


# --- Saved cards and subscriptions -------------------------------------------------


def test_payment_methods(auth_client: APIClient, patient_user: User) -> None:
    first = auth_client.post("/api/v1/payment-methods/", {"provider_token": "tkn"}, format="json")
    assert first.status_code == 201
    assert first.json() | {"id": None} == {
        "id": None,
        "brand": "Visa",
        "last4": "4242",
        "exp_month": 12,
        "exp_year": 2030,
        "is_default": True,
    }
    second = auth_client.post("/api/v1/payment-methods/", {"provider_token": "tkn"}).json()
    assert second["is_default"] is False
    rejected = auth_client.post("/api/v1/payment-methods/", {"provider_token": "fail"})
    assert rejected.status_code == 400
    assert auth_client.post(f"/api/v1/payment-methods/{second['id']}/default/").status_code == 204
    methods = auth_client.get("/api/v1/payment-methods/").json()
    assert [item["id"] for item in methods if item["is_default"]] == [second["id"]]
    assert auth_client.delete(f"/api/v1/payment-methods/{second['id']}/").status_code == 204
    remaining = auth_client.get("/api/v1/payment-methods/").json()
    assert len(remaining) == 1 and remaining[0]["is_default"] is True
    assert SavedPaymentMethod.objects.filter(is_deleted=True).count() == 1


def _subscribe(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> dict[str, Any]:
    body = checkout_body(
        treatment,
        address,
        package_id=str(package("Monthly Subscription").pk),
        payment={"method": "CULQI", "provider_token": "tkn", "save_payment_method": True},
    )
    order = place(auth_client, body)
    assert order.status_code == 201, order.json()
    culqi_post(api_client, culqi_event("charge.succeeded", charge_id(order.json()["id"])))
    subscriptions = auth_client.get("/api/v1/subscriptions/").json()
    assert len(subscriptions) == 1
    return subscriptions[0]


def test_subscription_lifecycle(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> None:
    sub = _subscribe(auth_client, api_client, treatment, address)
    assert sub["status"] == "ACTIVE"
    assert sub["package"]["name"] == "Monthly Subscription"
    assert sub["package"]["price"] == {"amount": "599.00", "currency": "PEN"}
    assert sub["payment_method"]["last4"] == "4242"
    assert sub["next_billing_at"] == sub["current_period_end"]
    url = f"/api/v1/subscriptions/{sub['id']}"

    method = SavedPaymentMethod.objects.get(user=treatment.patient)
    in_use = auth_client.delete(f"/api/v1/payment-methods/{method.pk}/")
    assert in_use.status_code == 409 and in_use.json()["code"] == "payment_method_in_use"

    too_far = (timezone.localdate() + timedelta(days=120)).isoformat()
    assert auth_client.post(f"{url}/pause/", {"resume_on": too_far}).status_code == 400
    resume_on = (timezone.localdate() + timedelta(days=10)).isoformat()
    paused = auth_client.post(f"{url}/pause/", {"resume_on": resume_on, "reason": "Trip"}).json()
    assert paused["status"] == "PAUSED" and paused["next_billing_at"] is None
    assert paused["paused_until"] == resume_on
    assert auth_client.post(f"{url}/pause/", {}).json()["code"] == "invalid_state_transition"
    resumed = auth_client.post(f"{url}/resume/").json()
    assert resumed["status"] == "ACTIVE" and resumed["next_billing_at"] is not None
    assert auth_client.post(f"{url}/resume/").status_code == 409

    cancelled = auth_client.post(f"{url}/cancel/", {"reason": "Done", "feedback": "Great"}).json()
    assert cancelled["cancel_at_period_end"] is True
    assert cancelled["status"] == "ACTIVE" and cancelled["next_billing_at"] is None
    assert auth_client.post(f"{url}/cancel/", {}).status_code == 409

    subscription = Subscription.objects.get(pk=sub["id"])
    result = order_services.run_subscription_jobs(
        now=subscription.current_period_end + timedelta(minutes=1)
    )
    assert result["cancelled"] == 1
    subscription.refresh_from_db()
    assert subscription.status == "CANCELLED"
    assert auth_client.get(f"{url}/").json()["status"] == "CANCELLED"


def test_subscription_renewal_success_and_retries(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> None:
    sub = _subscribe(auth_client, api_client, treatment, address)
    subscription = Subscription.objects.get(pk=sub["id"])
    first_end = subscription.current_period_end
    later = first_end + timedelta(minutes=1)

    result = order_services.run_subscription_jobs(now=later)
    assert result["renewed"] == 1
    renewal = Payment.objects.get(subscription=subscription, purpose="SUBSCRIPTION_RENEWAL")
    culqi_post(api_client, culqi_event("charge.succeeded", renewal.provider_payment_id))
    subscription.refresh_from_db()
    assert subscription.current_period_start == first_end
    assert subscription.next_billing_at == subscription.current_period_end
    renewal.refresh_from_db()
    renewal_order = renewal.order
    assert renewal_order is not None
    assert renewal_order.status == "PRESCRIPTION_VERIFIED"
    assert renewal_order.number == "ADL-ORD-00002"
    treatment.refresh_from_db()
    assert treatment.refills_remaining == 0
    assert Notification.objects.filter(type="SUBSCRIPTION_RENEWED").exists()
    assert Notification.objects.filter(type="REFILL_LOW").exists()

    # Next renewal: card declined synchronously, retried on the configured days.
    method = subscription.payment_method
    assert method is not None
    method.provider_card_id = "fail_card"
    method.save()
    order_services.run_subscription_jobs(now=subscription.current_period_end + timedelta(minutes=1))
    subscription.refresh_from_db()
    assert subscription.status == "PAST_DUE"
    assert subscription.renewal_failures == 1
    assert subscription.next_retry_at is not None
    assert Notification.objects.filter(type="SUBSCRIPTION_PAYMENT_FAILED").count() == 1
    for _ in range(3):
        order_services.run_subscription_jobs(now=subscription.next_retry_at + timedelta(seconds=1))
        subscription.refresh_from_db()
    assert subscription.renewal_failures == 4
    assert subscription.next_retry_at is None
    assert subscription.status == "PAST_DUE"
    # A past-due cancel takes effect immediately.
    response = auth_client.post(f"/api/v1/subscriptions/{subscription.pk}/cancel/", {})
    assert response.json()["status"] == "CANCELLED"


def test_renewal_without_refills_goes_on_hold(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> None:
    treatment.refills_total = treatment.refills_remaining = 1
    treatment.save()
    sub = _subscribe(auth_client, api_client, treatment, address)
    subscription = Subscription.objects.get(pk=sub["id"])
    order_services.run_subscription_jobs(now=subscription.current_period_end + timedelta(minutes=1))
    renewal = Payment.objects.get(subscription=subscription, purpose="SUBSCRIPTION_RENEWAL")
    culqi_post(api_client, culqi_event("charge.succeeded", renewal.provider_payment_id))
    renewal.refresh_from_db()
    assert renewal.order is not None and renewal.order.status == "ON_HOLD"


def test_renewal_failure_webhook_and_auto_resume(
    auth_client: APIClient, api_client: APIClient, treatment: Treatment, address: Any
) -> None:
    sub = _subscribe(auth_client, api_client, treatment, address)
    subscription = Subscription.objects.get(pk=sub["id"])
    order_services.run_subscription_jobs(now=subscription.current_period_end + timedelta(minutes=1))
    renewal = Payment.objects.get(subscription=subscription, purpose="SUBSCRIPTION_RENEWAL")
    culqi_post(api_client, culqi_event("charge.failed", renewal.provider_payment_id))
    subscription.refresh_from_db()
    assert subscription.status == "PAST_DUE"

    other = PatientFactory()
    give_consents(other)
    paused = Subscription.objects.create(
        user=other,
        package=subscription.package,
        treatment=subscription.treatment,
        status="PAUSED",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=5),
        paused_until=timezone.localdate(),
    )
    assert order_services.run_subscription_jobs()["resumed"] == 1
    paused.refresh_from_db()
    assert paused.status == "ACTIVE"


def test_subscription_and_card_endpoints_are_patient_only(staff_client: APIClient) -> None:
    assert staff_client.get("/api/v1/subscriptions/").status_code == 403
    assert staff_client.get("/api/v1/payment-methods/").status_code == 403


def test_placed_order_completes_journey_and_blocks_reorder(
    auth_client: APIClient, patient_user: User, physician: User, address: Any
) -> None:
    from apps.assessments.models import Prescription

    give_consents(patient_user)
    assessment, evaluation = make_evaluation(patient_user, "APPROVED", physician=physician)
    Prescription.objects.get_or_create(
        evaluation=evaluation,
        defaults={"medication_name": "Semaglutide", "dosage_label": "0.25 mg"},
    )
    treatment = make_treatment(patient_user, physician, evaluation=evaluation)

    def state() -> tuple[str, bool]:
        detail = auth_client.get(f"/api/v1/assessments/{assessment.pk}/").json()
        order_step = next(item["state"] for item in detail["journey"] if item["step"] == "ORDER")
        evaluation_view = auth_client.get(f"/api/v1/assessments/{assessment.pk}/evaluation/").json()
        return order_step, evaluation_view["prescription"]["can_order"]

    assert state() == ("ACTIVE", True)

    response = place(auth_client, checkout_body(treatment, address))
    assert response.status_code == 201, response.json()
    # An unpaid order does not count: the patient can still finish or retry checkout.
    assert state() == ("ACTIVE", True)

    Order.objects.filter(pk=response.json()["id"]).update(status="PRESCRIPTION_VERIFIED")
    assert state() == ("DONE", False)
