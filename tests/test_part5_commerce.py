"""Part 5 sections 6 and 7: tickets, complaints, orders, subscriptions and refunds."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Payment
from apps.catalog.models import TicketCategory
from apps.orders.models import Order, Subscription
from apps.payments.models import Refund
from apps.support.models import ComplaintBookEntry, SupportTicket
from apps.support.tickets import auto_close_resolved
from apps.treatments.models import Treatment
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
)
from tests.factories.part5 import make_staff
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db

ORDERS = "/api/v1/staff/orders/"
TICKETS = "/api/v1/staff/tickets/"


@pytest.fixture
def ops_user() -> User:
    return make_staff("OPERATIONS", name="Olga Ops")


@pytest.fixture
def ops(ops_user: User, make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(ops_user)


@pytest.fixture
def support(make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(make_staff("SUPPORT_AGENT", name="Sam Support"))


@pytest.fixture
def nurse(make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(make_staff("NURSE", name="Nora Nurse"))


@pytest.fixture
def treatment(patient_user: User) -> Treatment:
    doctor = make_physician()
    give_consents(patient_user)
    _, evaluation = make_evaluation(patient_user, "APPROVED", physician=doctor)
    return make_treatment(patient_user, doctor, evaluation=evaluation)


def paid_order(
    auth_client: APIClient, treatment: Treatment, *, package_name: str = "Custom 3-Month Protocol"
) -> dict[str, Any]:
    address = make_address(treatment.patient)
    body = {
        "treatment_id": str(treatment.pk),
        "package_id": str(package(package_name).pk),
        "shipping_address_id": str(address.pk),
        "payment": {"method": "CULQI", "provider_token": "tkn", "save_payment_method": True},
    }
    response = auth_client.post("/api/v1/orders/", body, format="json", HTTP_IDEMPOTENCY_KEY=idem())
    assert response.status_code == 201, response.json()
    order = response.json()
    charge = Payment.objects.filter(order_id=order["id"]).latest("created_at").provider_payment_id
    culqi_post(APIClient(), culqi_event("charge.succeeded", charge))
    return order


# --- Tickets (6.2, 6.3) --------------------------------------------------------------


def test_patient_ticket_lifecycle(
    auth_client: APIClient,
    support: APIClient,
    nurse: APIClient,
    api_client: APIClient,
    django_capture_on_commit_callbacks: Any,
) -> None:
    categories = api_client.get("/api/v1/support-ticket-categories/").json()
    assert {"code": "MEDICATION_GUIDANCE", "label": categories[3]["label"]} in categories or len(
        categories
    ) == 6
    short = auth_client.post(
        "/api/v1/support-tickets/",
        {"subject": "Hi", "category_code": "ORDERING", "message": "short"},
        format="json",
    )
    assert short.status_code == 400
    created = auth_client.post(
        "/api/v1/support-tickets/",
        {
            "subject": "Where is my order?",
            "category_code": "SHIPPING_COLD_CHAIN",
            "message": "<b>My order</b> has not arrived yet, it is warm.",
        },
        format="json",
    )
    assert created.status_code == 201, created.json()
    ticket = created.json()
    assert ticket["reference"].startswith("TCK-") and ticket["status"] == "OPEN"
    assert ticket["source"] == "PATIENT"
    url = f"/api/v1/support-tickets/{ticket['id']}/"
    assert auth_client.get("/api/v1/support-tickets/?status=OPEN").json()["count"] == 1

    staff_list = support.get(TICKETS).json()
    assert staff_list["summary"]["open"] == 1 and staff_list["count"] == 1
    assert support.get(f"{TICKETS}?assigned_to=unassigned").json()["count"] == 1
    assert support.get(f"{TICKETS}?assigned_to=not-a-uuid").status_code == 400

    note = support.post(
        f"{TICKETS}{ticket['id']}/reply/",
        {"body": "Checked carrier", "is_internal": True},
        format="json",
    )
    assert note.status_code == 201 and note.json()["is_internal"] is True
    with django_capture_on_commit_callbacks(execute=True):
        reply = support.post(
            f"{TICKETS}{ticket['id']}/reply/", {"body": "It ships today."}, format="json"
        )
    assert reply.status_code == 201
    detail = support.get(f"{TICKETS}{ticket['id']}/").json()
    assert detail["status"] == "AWAITING_CUSTOMER" and detail["first_response_at"]
    summary = support.get(TICKETS).json()["summary"]
    assert summary["open"] == 0 and summary["awaiting_customer"] == 1
    assert len(detail["messages"]) == 3 and detail["history"]
    chat = support.get(f"{TICKETS}{ticket['id']}/?view=CHAT").json()
    assert "history" not in chat

    patient_view = auth_client.get(url).json()
    assert [m["is_internal"] for m in patient_view["messages"]] == [False, False]
    assert "<b>" not in patient_view["messages"][0]["body"]
    again = auth_client.post(f"{url}messages/", {"body": "Thanks!"}, format="json")
    assert again.status_code == 201
    assert auth_client.get(url).json()["status"] == "OPEN"

    staff_member = make_staff("SUPPORT_AGENT", name="Assignee")
    assigned = support.post(
        f"{TICKETS}{ticket['id']}/assign/", {"staff_id": str(staff_member.pk)}, format="json"
    )
    assert assigned.status_code == 200 and assigned.json()["assigned_to"]["name"] == "Assignee"
    assert (
        support.post(
            f"{TICKETS}{ticket['id']}/priority/", {"priority": "URGENT"}, format="json"
        ).json()["priority"]
        == "URGENT"
    )
    resolved = support.post(
        f"{TICKETS}{ticket['id']}/status/", {"status": "RESOLVED"}, format="json"
    )
    assert resolved.json()["status"] == "RESOLVED" and resolved.json()["resolved_at"]
    same = support.post(f"{TICKETS}{ticket['id']}/status/", {"status": "RESOLVED"}, format="json")
    assert same.status_code == 409

    SupportTicket.objects.filter(pk=ticket["id"]).update(resolved_at="2020-01-01T00:00:00Z")
    assert auto_close_resolved() == 1
    closed = auth_client.post(f"{url}messages/", {"body": "Hello?"}, format="json")
    assert closed.status_code == 409
    assert auth_client.post(f"{url}close/").status_code == 409

    # Clinical tickets are restricted to clinical staff.
    medical = auth_client.post(
        "/api/v1/support-tickets/",
        {
            "subject": "Dose question",
            "category_code": "MEDICATION_GUIDANCE",
            "message": "Can I take my dose with food?",
        },
        format="json",
    ).json()
    row = next(r for r in support.get(TICKETS).json()["results"] if r["id"] == medical["id"])
    assert row["subject"] == "Clinical: restricted"
    assert support.get(f"{TICKETS}{medical['id']}/").status_code == 404
    assert nurse.get(f"{TICKETS}{medical['id']}/").json()["subject"] == "Dose question"
    dashboard = nurse.get("/api/v1/staff/dashboard/").json()
    assert dashboard["stats"]["open_tickets_count"] >= 1
    assert auth_client.post(f"/api/v1/support-tickets/{medical['id']}/close/").status_code == 200
    assert mail.outbox is not None


def test_ticket_create_throttle(auth_client: APIClient) -> None:
    body = {
        "subject": "Billing",
        "category_code": "BILLING",
        "message": "Please help with billing.",
    }
    for _ in range(10):
        assert auth_client.post("/api/v1/support-tickets/", body, format="json").status_code == 201
    assert auth_client.post("/api/v1/support-tickets/", body, format="json").status_code == 429
    cache.clear()


def test_contact_ticket_reply_is_emailed(
    support: APIClient, api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        api_client.post(
            "/api/v1/public/contact-messages/",
            {"full_name": "Maria", "email": "maria@example.com", "message": "How do I sign up?"},
            format="json",
        )
    ticket = SupportTicket.objects.get()
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        support.post(f"{TICKETS}{ticket.pk}/reply/", {"body": "Visit our site."}, format="json")
    assert any("Visit our site." in message.body for message in mail.outbox)


# --- Complaints (6.4) ----------------------------------------------------------------


def test_complaints_book(
    support: APIClient, api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    created = api_client.post(
        "/api/v1/public/complaints/",
        {
            "full_name": "Maria Gonzalez",
            "email": "maria@example.com",
            "category_code": "SERVICE",
            "filing_type": "RECLAMO",
            "description": "The consultation did not start at the scheduled time.",
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    ).json()
    number = created["complaint_number"]
    url = f"/api/v1/staff/complaints/{number}/"
    listing = support.get("/api/v1/staff/complaints/").json()
    assert listing["summary"] == {"open": 1, "overdue": 0}
    assert listing["results"][0]["is_overdue"] is False
    assert support.get("/api/v1/staff/complaints/?overdue=true").json()["count"] == 0

    early_close = support.post(f"{url}status/", {"status": "CLOSED"}, format="json")
    assert early_close.status_code == 409
    with django_capture_on_commit_callbacks(execute=True):
        responded = support.post(
            f"{url}respond/",
            {"body": "We apologise; the physician was delayed.", "resolution": "RESOLVED"},
            format="json",
        )
    assert responded.status_code == 201, responded.json()
    detail = support.get(url).json()
    assert detail["status"] == "RESOLVED" and detail["responded_at"]
    assert len(detail["responses"]) == 1 and detail["history"]
    assert any("physician was delayed" in message.body for message in mail.outbox)
    closed = support.post(f"{url}status/", {"status": "CLOSED", "note": "Done"}, format="json")
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    assert ComplaintBookEntry.objects.get().responses.count() == 1


# --- Orders, subscriptions and refunds (7) ---------------------------------------------


def test_staff_order_flow(
    auth_client: APIClient,
    ops: APIClient,
    support: APIClient,
    nurse: APIClient,
    treatment: Treatment,
) -> None:
    order = paid_order(auth_client, treatment)
    listing = support.get(ORDERS).json()
    assert listing["summary"] == {"processing": 1, "in_transit": 0, "delivered": 0}
    row = listing["results"][0]
    assert row["number"] == "ADL-ORD-00001" and row["status"] == "PRESCRIPTION_VERIFIED"
    assert row["product_label"]
    assert support.get(f"{ORDERS}?status_group=PROCESSING&search=ADL").json()["count"] == 1

    detail = support.get(f"{ORDERS}{order['id']}/").json()
    assert detail["prescription"] is None and detail["patient"]["email"]
    assert nurse.get(f"{ORDERS}{order['id']}/").status_code == 403

    denied = support.post(
        f"{ORDERS}{order['id']}/transition/",
        {"to_status": "COMPOUNDING"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert denied.status_code == 403

    def move(to_status: str, **extra: Any) -> Any:
        return ops.post(
            f"{ORDERS}{order['id']}/transition/",
            {"to_status": to_status, **extra},
            format="json",
            HTTP_IDEMPOTENCY_KEY=idem(),
        )

    assert move("DELIVERED").status_code == 409
    assert move("ON_HOLD", note="Address check").json()["status"] == "ON_HOLD"
    assert move("PRESCRIPTION_VERIFIED").json()["status"] == "PRESCRIPTION_VERIFIED"
    assert move("COMPOUNDING").json()["status"] == "COMPOUNDING"
    assert move("IN_TRANSIT").status_code == 400
    shipped = move(
        "IN_TRANSIT",
        tracking={"carrier": "Olva", "tracking_number": "OLV123", "tracking_url": None},
    )
    assert shipped.status_code == 200, shipped.json()
    fixed = ops.patch(
        f"{ORDERS}{order['id']}/tracking/",
        {"carrier": "Olva Courier", "tracking_number": "OLV124"},
        format="json",
    )
    assert fixed.status_code == 200
    note = ops.post(f"{ORDERS}{order['id']}/notes/", {"body": "Fragile"}, format="json")
    assert note.status_code == 201
    final = ops.get(f"{ORDERS}{order['id']}/").json()
    assert final["internal_notes"][0]["body"] == "Fragile"
    assert [row["to"] for row in final["status_history"]][-1] == "IN_TRANSIT"
    late = ops.post(f"{ORDERS}{order['id']}/cancel/", {"reason": "Too late"}, format="json")
    assert late.status_code == 409


def test_cancel_creates_refund_task_and_refund_limits(
    auth_client: APIClient,
    ops: APIClient,
    admin_client: APIClient,
    treatment: Treatment,
) -> None:
    order = paid_order(auth_client, treatment)
    cancelled = ops.post(
        f"{ORDERS}{order['id']}/cancel/", {"reason": "Out of stock"}, format="json"
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    task = SupportTicket.objects.get(source="STAFF")
    assert task.category.code == "BILLING" and str(task.order_id) == order["id"]

    payment = Payment.objects.filter(order_id=order["id"]).latest("created_at")
    url = f"/api/v1/staff/payments/{payment.pk}/refund/"
    no_key = ops.post(url, {"reason_code": "CUSTOMER_REQUEST"}, format="json")
    assert no_key.status_code in (400, 428)
    over_limit = ops.post(
        url, {"reason_code": "CUSTOMER_REQUEST"}, format="json", HTTP_IDEMPOTENCY_KEY=idem()
    )
    assert over_limit.status_code == 403
    assert over_limit.json()["details"]["required"] == "ADMIN"
    partial = ops.post(
        url,
        {"reason_code": "PRODUCT_ISSUE", "amount": {"amount": "100.00", "currency": "PEN"}},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert partial.status_code == 202, partial.json()
    assert partial.json() == {
        "payment_id": str(payment.pk),
        "status": "PENDING",
        "amount": {"amount": "100.00", "currency": "PEN"},
    }
    wrong_currency = admin_client.post(
        url,
        {"reason_code": "OTHER", "amount": {"amount": "1.00", "currency": "USD"}},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert wrong_currency.status_code == 400
    rest = admin_client.post(
        url, {"reason_code": "CUSTOMER_REQUEST"}, format="json", HTTP_IDEMPOTENCY_KEY=idem()
    )
    assert rest.status_code == 202
    assert rest.json()["amount"]["amount"] == f"{payment.amount - Decimal('100.00'):.2f}"
    assert Refund.objects.count() == 2
    nothing_left = admin_client.post(
        url, {"reason_code": "OTHER"}, format="json", HTTP_IDEMPOTENCY_KEY=idem()
    )
    assert nothing_left.status_code == 400

    culqi_post(
        APIClient(),
        culqi_event(
            "refund.succeeded", "", charge_id=payment.provider_payment_id, amount_refunded=10000
        ),
    )
    assert Refund.objects.filter(status="SUCCEEDED").count() == 1


@override_settings(REFILL_DUE_WINDOW_DAYS=40)
def test_staff_subscriptions(
    auth_client: APIClient, ops: APIClient, support: APIClient, treatment: Treatment
) -> None:
    paid_order(auth_client, treatment, package_name="Monthly Subscription")
    subscription = Subscription.objects.get()
    url = f"/api/v1/staff/subscriptions/{subscription.pk}/"
    listing = support.get("/api/v1/staff/subscriptions/").json()
    assert listing["summary"]["active"] == 1
    row = listing["results"][0]
    assert row["package_name"] == "Monthly Subscription"
    assert row["price_per_month"]["currency"] == "PEN"
    assert support.get("/api/v1/staff/subscriptions/?scope=REFILL_DUE").json()["count"] == 1
    detail = support.get(url).json()
    assert detail["orders"] and detail["payments"]

    assert support.post(f"{url}pause/", {"reason": "Trip"}, format="json").status_code == 403
    paused = ops.post(f"{url}pause/", {"reason": "Patient travelling"}, format="json")
    assert paused.status_code == 200 and paused.json()["status"] == "PAUSED"
    refill_paused = ops.post(f"{url}new-refill/", HTTP_IDEMPOTENCY_KEY=idem())
    assert refill_paused.status_code == 409
    assert (
        ops.post(f"{url}resume/", {"reason": "Back home"}, format="json").json()["status"]
        == "ACTIVE"
    )

    refill = ops.post(f"{url}new-refill/", HTTP_IDEMPOTENCY_KEY=idem())
    assert refill.status_code == 201, refill.json()
    assert refill.json()["status"] == "PENDING_PAYMENT"
    assert Order.objects.filter(subscription=subscription).count() == 2

    cancelled = ops.post(f"{url}cancel/", {"reason": "Patient request"}, format="json")
    assert cancelled.status_code == 200


def test_refund_task_on_not_suitable_when_enabled(
    make_auth_client: Callable[[User], APIClient], admin_client: APIClient
) -> None:
    put = admin_client.put(
        "/api/v1/admin/settings/payments/",
        {
            "refund_approval_limit": {"amount": "500.00", "currency": "PEN"},
            "refund_fee_on_not_suitable": True,
            "reason": "Money-back guarantee launch",
        },
        format="json",
    )
    assert put.status_code == 200, put.json()
    doctor = make_physician()
    _, evaluation = make_evaluation(PatientFactory(), "IN_REVIEW", physician=doctor)
    assert evaluation is not None
    response = make_auth_client(doctor).post(
        f"/api/v1/clinical/evaluations/{evaluation.pk}/reject/",
        {
            "reason_code": "IN_PERSON_CARE_RECOMMENDED",
            "internal_note": "Requires in-person thyroid workup.",
            "attestation": True,
        },
        format="json",
        HTTP_IF_MATCH=f'"{evaluation.version}"',
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert response.status_code == 200, response.json()
    assert SupportTicket.objects.filter(
        category=TicketCategory.objects.get(code="BILLING")
    ).exists()
