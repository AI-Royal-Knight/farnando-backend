"""Contract Part 3 section 4: consultations, availability, video, jobs, video webhook."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.enums import PaymentPurpose, PaymentStatus
from apps.assessments.models import Payment
from apps.consultations import services
from apps.consultations.models import AvailabilityBlock, Consultation, ConsultationStatus
from apps.core.models import AuditLog
from apps.notifications.models import Notification
from apps.payments.models import WebhookEvent
from tests.factories.part3 import (
    consultation_type,
    daily_post,
    give_consents,
    idem,
    make_consultation,
    make_evaluation,
    make_physician,
)
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def physician(db: Any) -> User:
    return make_physician()


@pytest.fixture
def eligible(patient_user: User, physician: User) -> User:
    give_consents(patient_user)
    make_evaluation(patient_user, "SUBMITTED", physician=physician)
    return patient_user


def day(offset: int) -> str:
    return (timezone.localdate() + timedelta(days=offset)).isoformat()


def slots(client: APIClient, physician: User, *, code: str = "INITIAL", offset: int = 2) -> Any:
    response = client.get(
        f"/api/v1/physicians/{physician.pk}/availability/",
        {
            "from": day(offset),
            "to": day(offset),
            "consultation_type_id": consultation_type(code).pk,
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()["slots"]


def book(client: APIClient, physician: User, start: str, *, code: str = "INITIAL") -> Any:
    return client.post(
        "/api/v1/consultations/",
        {
            "physician_id": str(physician.pk),
            "consultation_type_id": str(consultation_type(code).pk),
            "start": start,
            "patient_note": "<b>Hola</b> doctor",
            "demo_payment": True,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )


def test_consultation_types(auth_client: APIClient) -> None:
    body = auth_client.get("/api/v1/consultation-types/").json()
    assert [item["code"] for item in body] == ["INITIAL", "METABOLIC_RESET", "FOLLOW_UP"]
    assert body[0]["is_initial"] is True and body[0]["duration_minutes"] == 30


def test_physician_list_and_search(auth_client: APIClient, physician: User) -> None:
    make_physician(name="Lucia Paz", specialty="Nutrition", license_label="CMP 777")
    make_physician(name="Hidden Doc", accepts=False)
    body = auth_client.get("/api/v1/physicians/").json()
    assert body["count"] == 2
    item = body["results"][1]
    assert item["name"] == "Dr. Marcus Vance, MD"
    assert item["license_label"] == "CMP 12345"
    assert item["next_available_at"] is not None
    assert auth_client.get("/api/v1/physicians/?search=777").json()["count"] == 1
    assert auth_client.get("/api/v1/physicians/?specialty=nutrition").json()["count"] == 1
    within = auth_client.get("/api/v1/physicians/?available_within_days=7").json()
    assert within["count"] == 2
    assert auth_client.get("/api/v1/physicians/?available_within_days=40").status_code == 400
    assert auth_client.get("/api/v1/physicians/?available_within_days=x").status_code == 400


def test_physician_list_is_patient_only(staff_client: APIClient) -> None:
    assert staff_client.get("/api/v1/physicians/").status_code == 403


def test_availability_rules_blocks_and_lead_time(auth_client: APIClient, physician: User) -> None:
    initial = slots(auth_client, physician)
    # 08:00-20:00 Lima in 30-minute slots.
    assert len(initial) == 24
    assert initial[0]["start"].endswith("13:00:00Z")
    reset = slots(auth_client, physician, code="METABOLIC_RESET")
    assert len(reset) == 16

    first = timezone.datetime.fromisoformat(initial[0]["start"].replace("Z", "+00:00"))
    AvailabilityBlock.objects.create(
        physician=physician, start=first, end=first + timedelta(hours=1), reason="Lunch"
    )
    assert len(slots(auth_client, physician)) == 22

    today = slots(auth_client, physician, offset=0)
    now = timezone.now()
    assert all(
        timezone.datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
        >= now + timedelta(hours=12)
        for item in today
    )
    response = auth_client.get(
        f"/api/v1/physicians/{physician.pk}/availability/",
        {"from": day(1), "to": day(1), "consultation_type_id": consultation_type().pk},
    )
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "params",
    [
        {"to": "2026-12-01"},
        {"from": "2026-12-01", "to": "2026-11-01"},
        {"from": "2026-11-01", "to": "2026-12-15"},
        {"from": "bad", "to": "2026-12-01"},
        {"from": "2026-11-01", "to": "2026-11-02"},
        {"from": "2026-11-01", "to": "2026-11-02", "consultation_type_id": "nope"},
    ],
)
def test_availability_validation(
    auth_client: APIClient, physician: User, params: dict[str, str]
) -> None:
    response = auth_client.get(f"/api/v1/physicians/{physician.pk}/availability/", params)
    assert response.status_code == 400


def test_availability_unknown_physician(auth_client: APIClient) -> None:
    other = make_physician(name="Off Duty", accepts=False)
    response = auth_client.get(f"/api/v1/physicians/{other.pk}/availability/")
    assert response.status_code == 404


def test_booking_flow(auth_client: APIClient, eligible: User, physician: User) -> None:
    start = slots(auth_client, physician)[0]["start"]
    response = book(auth_client, physician, start)
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "REQUESTED"
    assert body["start"] == start
    assert body["duration_minutes"] == 30
    assert body["patient_note"] == "Hola doctor"
    assert body["physician"]["name"] == "Dr. Marcus Vance, MD"
    assert body["consultation_type"]["name"] == "Initial Consultation"
    assert body["can_join"] is False
    assert body["can_reschedule"] is True and body["can_cancel"] is True
    assert body["timezone"] == "America/Lima"
    # The slot is now held.
    assert start not in [item["start"] for item in slots(auth_client, physician)]

    duplicate = book(auth_client, physician, slots(auth_client, physician)[0]["start"])
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "conflict"

    upcoming = auth_client.get("/api/v1/consultations/?scope=UPCOMING").json()
    assert upcoming["count"] == 1
    assert auth_client.get("/api/v1/consultations/?scope=PAST").json()["count"] == 0
    detail = auth_client.get(f"/api/v1/consultations/{body['id']}/")
    assert detail.status_code == 200

    consultation = Consultation.objects.get(pk=body["id"])
    services.physician_accept(consultation, actor=physician)
    consultation.refresh_from_db()
    assert consultation.status == "CONFIRMED" and consultation.confirmed_at is not None
    assert Notification.objects.filter(user=eligible, type="CONSULTATION_CONFIRMED").exists()


def test_booking_idempotent_replay(auth_client: APIClient, eligible: User, physician: User) -> None:
    start = slots(auth_client, physician)[0]["start"]
    key = idem()
    payload = {
        "physician_id": str(physician.pk),
        "consultation_type_id": str(consultation_type().pk),
        "start": start,
        "demo_payment": True,
    }
    first = auth_client.post(
        "/api/v1/consultations/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    second = auth_client.post(
        "/api/v1/consultations/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    missing = auth_client.post("/api/v1/consultations/", payload, format="json")
    assert missing.json()["code"] == "idempotency_key_required"


def test_booking_requires_evaluation_and_consent(
    auth_client: APIClient, patient_user: User, physician: User
) -> None:
    start = slots(auth_client, physician)[0]["start"]
    response = book(auth_client, physician, start)
    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_required"
    make_evaluation(patient_user, "AWAITING_PAYMENT")
    assert book(auth_client, physician, start).json()["code"] == "evaluation_required"
    make_evaluation(patient_user, "APPROVED", physician=physician)
    response = book(auth_client, physician, start)
    assert response.status_code == 403
    assert response.json()["code"] == "consent_required"


def test_booking_validation(auth_client: APIClient, eligible: User, physician: User) -> None:
    start = slots(auth_client, physician)[0]["start"]
    off = make_physician(name="Off Duty", accepts=False)
    assert book(auth_client, off, start).status_code == 400
    taken = book(auth_client, physician, start.replace(":00:00Z", ":05:00Z"))
    assert taken.status_code == 409
    assert taken.json()["code"] == "slot_unavailable"
    other_assessment, _ = make_evaluation(PatientFactory(), "SUBMITTED")
    response = auth_client.post(
        "/api/v1/consultations/",
        {
            "physician_id": str(physician.pk),
            "consultation_type_id": str(consultation_type().pk),
            "start": start,
            "assessment_id": str(other_assessment.pk),
            "demo_payment": True,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert response.status_code == 400


def test_booking_lost_race_returns_slot_unavailable(
    auth_client: APIClient, eligible: User, physician: User
) -> None:
    start = slots(auth_client, physician)[0]["start"]
    with mock.patch(
        "apps.consultations.services.Consultation.objects.create",
        side_effect=IntegrityError("consultations_slot_held_once"),
    ):
        response = book(auth_client, physician, start)
    assert response.status_code == 409
    assert response.json()["code"] == "slot_unavailable"


def test_reschedule_and_cancel(auth_client: APIClient, eligible: User, physician: User) -> None:
    open_slots = slots(auth_client, physician, offset=3)
    created = book(auth_client, physician, open_slots[0]["start"]).json()
    consultation = Consultation.objects.get(pk=created["id"])
    services.physician_accept(consultation, actor=physician)

    new_start = open_slots[4]["start"]
    response = auth_client.post(
        f"/api/v1/consultations/{created['id']}/reschedule/",
        {"start": new_start},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "REQUESTED"
    assert response.json()["start"] == new_start
    assert response.json()["confirmed_at"] is None
    assert open_slots[0]["start"] in [
        item["start"] for item in slots(auth_client, physician, offset=3)
    ]

    cancelled = auth_client.post(
        f"/api/v1/consultations/{created['id']}/cancel/", {"reason": "Travel"}, format="json"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    again = auth_client.post(f"/api/v1/consultations/{created['id']}/cancel/", {}, format="json")
    assert again.json()["code"] == "invalid_state_transition"
    assert consultation.status_history.count() == 4


def test_too_late_to_change(auth_client: APIClient, patient_user: User, physician: User) -> None:
    soon = make_consultation(patient_user, physician, start_in=timedelta(hours=1))
    response = auth_client.post(f"/api/v1/consultations/{soon.pk}/cancel/", {}, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "too_late_to_change"
    reschedule = auth_client.post(
        f"/api/v1/consultations/{soon.pk}/reschedule/",
        {"start": (timezone.now() + timedelta(days=3)).isoformat()},
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert reschedule.json()["code"] == "too_late_to_change"
    later = make_consultation(
        patient_user, physician, start_in=timedelta(hours=10), code="FOLLOW_UP"
    )
    assert auth_client.post(f"/api/v1/consultations/{later.pk}/cancel/", {}).status_code == 200


def test_join_window_and_leave(auth_client: APIClient, patient_user: User, physician: User) -> None:
    future = make_consultation(patient_user, physician, start_in=timedelta(hours=2))
    closed = auth_client.post(f"/api/v1/consultations/{future.pk}/join/")
    assert closed.status_code == 409
    assert closed.json()["code"] == "join_window_closed"
    assert "join_opens_at" in closed.json()["details"]

    now = make_consultation(
        patient_user, physician, start_in=timedelta(minutes=5), code="FOLLOW_UP"
    )
    response = auth_client.post(f"/api/v1/consultations/{now.pk}/join/")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["provider"] == "DAILY"
    assert body["room_url"].endswith(f"consultation-{now.pk.hex}")
    assert body["token"].startswith("fake_")
    assert body["consultation"]["can_join"] is True
    assert AuditLog.objects.filter(action="consultation.joined").exists()
    # Room is created once and reused.
    again = auth_client.post(f"/api/v1/consultations/{now.pk}/join/").json()
    assert again["room_url"] == body["room_url"]

    assert auth_client.post(f"/api/v1/consultations/{now.pk}/leave/").status_code == 204
    now.refresh_from_db()
    assert now.patient_left_at is not None
    assert now.status == "CONFIRMED"
    requested = make_consultation(
        patient_user,
        physician,
        start_in=timedelta(days=2),
        status="REQUESTED",
        code="METABOLIC_RESET",
    )
    assert auth_client.post(f"/api/v1/consultations/{requested.pk}/leave/").status_code == 409


def test_consultation_scoping_and_admin_audit(
    auth_client: APIClient, admin_client: APIClient, physician: User
) -> None:
    other = PatientFactory()
    consultation = make_consultation(other, physician, start_in=timedelta(days=2))
    assert auth_client.get(f"/api/v1/consultations/{consultation.pk}/").status_code == 404
    assert auth_client.post(f"/api/v1/consultations/{consultation.pk}/join/").status_code == 404
    assert admin_client.get(f"/api/v1/consultations/{consultation.pk}/").status_code == 200
    assert admin_client.get("/api/v1/consultations/").json()["count"] == 1
    assert AuditLog.objects.filter(action="consultation.read").exists()
    assert admin_client.post("/api/v1/consultations/", {}, format="json").status_code == 403


def test_list_filters(auth_client: APIClient, patient_user: User, physician: User) -> None:
    make_consultation(patient_user, physician, start_in=timedelta(days=2))
    make_consultation(
        patient_user, physician, start_in=-timedelta(days=2), status="COMPLETED", code="FOLLOW_UP"
    )
    assert auth_client.get("/api/v1/consultations/").json()["count"] == 2
    assert auth_client.get("/api/v1/consultations/?status=COMPLETED").json()["count"] == 1
    assert auth_client.get("/api/v1/consultations/?status=NOPE").status_code == 400
    assert auth_client.get("/api/v1/consultations/?scope=SOON").status_code == 400


def test_state_machine_rejects_invalid(patient_user: User, physician: User) -> None:
    consultation = make_consultation(
        patient_user, physician, start_in=timedelta(days=2), status="COMPLETED"
    )
    from apps.core.exceptions import InvalidStateTransition

    with pytest.raises(InvalidStateTransition):
        services.physician_accept(consultation, actor=physician)
    requested = make_consultation(
        patient_user, physician, start_in=timedelta(days=3), status="REQUESTED", code="FOLLOW_UP"
    )
    services.physician_decline(requested, actor=physician, reason="Unavailable")
    assert Notification.objects.filter(type="CONSULTATION_DECLINED").exists()


def test_scheduled_jobs(patient_user: User, physician: User) -> None:
    stale = make_consultation(
        patient_user, physician, start_in=timedelta(hours=1), status="REQUESTED"
    )
    ghost = make_consultation(
        patient_user, physician, start_in=-timedelta(hours=2), code="FOLLOW_UP"
    )
    soon = make_consultation(
        patient_user, physician, start_in=timedelta(hours=20), code="METABOLIC_RESET"
    )
    from apps.consultations.tasks import run_consultation_jobs

    result = run_consultation_jobs()
    assert result == {"expired": 1, "no_show": 1, "reminders": 1}
    stale.refresh_from_db()
    ghost.refresh_from_db()
    soon.refresh_from_db()
    assert stale.status == ConsultationStatus.EXPIRED
    assert ghost.status == ConsultationStatus.NO_SHOW
    assert soon.reminder_24h_sent_at is not None and soon.reminder_1h_sent_at is None
    assert services.send_reminders() == 0
    assert services.send_reminders(now=timezone.now() + timedelta(hours=19, minutes=30)) == 1
    assert Notification.objects.filter(type="CONSULTATION_REMINDER").count() == 2


def test_video_webhook(api_client: APIClient, patient_user: User, physician: User) -> None:
    consultation = make_consultation(patient_user, physician, start_in=timedelta(minutes=2))
    consultation.video_room_name = "consultation-room-1"
    consultation.save()
    payload = {
        "id": "evt-1",
        "type": "participant.joined",
        "payload": {"room": "consultation-room-1"},
    }
    assert daily_post(api_client, payload).status_code == 200
    consultation.refresh_from_db()
    assert consultation.status == "IN_PROGRESS" and consultation.started_at is not None
    # Replays are stored once and not processed again.
    assert daily_post(api_client, payload).status_code == 200
    assert WebhookEvent.objects.filter(provider="DAILY").count() == 1

    ended = {"id": "evt-2", "type": "meeting.ended", "payload": {"room": "consultation-room-1"}}
    assert daily_post(api_client, ended).status_code == 200
    consultation.refresh_from_db()
    assert consultation.ended_at is not None and consultation.status == "IN_PROGRESS"
    services.physician_complete(consultation, actor=physician)
    consultation.refresh_from_db()
    assert consultation.status == "COMPLETED"

    unknown = {"id": "evt-3", "type": "participant.joined", "payload": {"room": "missing"}}
    assert daily_post(api_client, unknown).status_code == 200
    assert WebhookEvent.objects.get(provider_event_id="evt-3").processing_error


def test_video_webhook_rejects_bad_signature(api_client: APIClient) -> None:
    response = api_client.post(
        "/api/v1/webhooks/video/daily/",
        data=b'{"id": "x", "type": "meeting.ended"}',
        content_type="application/json",
        HTTP_X_WEBHOOK_TIMESTAMP="1",
        HTTP_X_WEBHOOK_SIGNATURE="bad",
    )
    assert response.status_code == 401
    assert WebhookEvent.objects.count() == 0


def test_unused_fixture_helper(make_auth_client: Callable[[User], APIClient]) -> None:
    assert callable(make_auth_client)


def test_booking_charges_the_consultation_fee(
    auth_client: APIClient, eligible: User, physician: User
) -> None:
    start = slots(auth_client, physician)[0]["start"]
    response = book(auth_client, physician, start)
    assert response.status_code == 201, response.json()

    consultation = Consultation.objects.get(pk=response.json()["id"])
    payment = Payment.objects.get(consultation=consultation)
    assert payment.purpose == PaymentPurpose.CONSULTATION_FEE
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.amount == consultation_type().fee_amount
    assert payment.provider_payment_id.startswith("demo_")


def test_booking_a_paid_visit_requires_payment(
    auth_client: APIClient, eligible: User, physician: User
) -> None:
    start = slots(auth_client, physician)[0]["start"]
    response = auth_client.post(
        "/api/v1/consultations/",
        {
            "physician_id": str(physician.pk),
            "consultation_type_id": str(consultation_type().pk),
            "start": start,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=idem(),
    )
    assert response.status_code == 400
    assert not Consultation.objects.filter(patient=eligible).exists()
