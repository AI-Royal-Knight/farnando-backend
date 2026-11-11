"""Contract Part 3 sections 0, 3, 8 and 9: overview, messaging, notifications."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.services import EvaluationService
from apps.core.models import AuditLog
from apps.messaging.models import Message
from apps.messaging.services import send_message
from apps.notifications.models import Notification
from apps.notifications.services import notify
from apps.orders.models import Order
from apps.patients.models import PatientProfile
from tests.factories.part3 import (
    give_consents,
    idem,
    make_consultation,
    make_evaluation,
    make_nurse,
    make_physician,
    make_treatment,
    package,
)
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def physician(db: Any) -> User:
    return make_physician()


@pytest.fixture
def care_patient(patient_user: User, physician: User) -> User:
    give_consents(patient_user)
    make_evaluation(patient_user, "IN_REVIEW", physician=physician)
    return patient_user


def open_thread(client: APIClient, participant: User, body: str | None = None) -> Any:
    payload: dict[str, Any] = {"participant_id": str(participant.pk)}
    if body is not None:
        payload["body"] = body
    return client.post("/api/v1/message-threads/", payload, format="json")


# --- Messaging --------------------------------------------------------------------


def test_thread_get_or_create(auth_client: APIClient, care_patient: User, physician: User) -> None:
    created = open_thread(auth_client, physician, "Hola doctor")
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["participant"]["role"] == "PHYSICIAN"
    assert body["participant"]["name"] == "Dr. Marcus Vance, MD"
    assert body["last_message"]["preview"] == "Hola doctor"
    assert body["last_message"]["sender_role"] == "PATIENT"
    assert body["unread_count"] == 0
    assert "emergency" in body["safety_notice"]
    again = open_thread(auth_client, physician)
    assert again.status_code == 200 and again.json()["id"] == body["id"]
    stranger = make_physician(name="Not Mine")
    denied = open_thread(auth_client, stranger)
    assert denied.status_code == 403 and denied.json()["code"] == "not_in_care_team"


def test_nurse_thread(auth_client: APIClient, care_patient: User) -> None:
    nurse = make_nurse()
    profile, _ = PatientProfile.objects.get_or_create(user=care_patient)
    profile.assigned_nurse = nurse
    profile.save()
    thread = open_thread(auth_client, nurse).json()
    assert thread["participant"]["role"] == "NURSE"
    from apps.messaging.models import MessageThread

    message = send_message(
        thread=MessageThread.objects.get(pk=thread["id"]), sender=nurse, body="Hi, I am Sarah"
    )
    assert message.sender_role == "NURSE"


def test_messages_flow(auth_client: APIClient, care_patient: User, physician: User) -> None:
    thread = open_thread(auth_client, physician).json()
    url = f"/api/v1/message-threads/{thread['id']}/messages/"
    sent = auth_client.post(url, {"body": "  <script>x</script>Tengo una duda  "}, format="json")
    assert sent.status_code == 201
    assert sent.json()["body"] == "xTengo una duda"
    assert sent.json()["is_mine"] is True
    # The physician's notification never contains the text.
    note = Notification.objects.get(user=physician, type="NEW_MESSAGE")
    assert "duda" not in note.title + note.body

    from apps.messaging.models import MessageThread

    db_thread = MessageThread.objects.get(pk=thread["id"])
    reply = send_message(thread=db_thread, sender=physician, body="Claro, cuéntame")
    patient_note = Notification.objects.get(user=care_patient, type="NEW_MESSAGE")
    assert "Claro" not in patient_note.title + patient_note.body

    listing = auth_client.get(url)
    assert listing.status_code == 200
    etag = listing["ETag"]
    results = listing.json()["results"]
    assert [item["body"] for item in results] == ["Claro, cuéntame", "xTengo una duda"]
    assert results[0]["sender"]["role"] == "PHYSICIAN" and results[0]["is_mine"] is False
    assert auth_client.get(url, HTTP_IF_NONE_MATCH=etag).status_code == 304

    assert auth_client.get("/api/v1/messages/unread-count/").json() == {"count": 1}
    threads = auth_client.get("/api/v1/message-threads/").json()
    assert threads["results"][0]["unread_count"] == 1
    assert auth_client.get("/api/v1/message-threads/?filter=UNREAD").json()["count"] == 1

    read = auth_client.post(
        f"/api/v1/message-threads/{thread['id']}/read/",
        {"up_to_message_id": str(reply.pk)},
        format="json",
    )
    assert read.status_code == 204
    assert auth_client.get("/api/v1/messages/unread-count/").json() == {"count": 0}
    assert auth_client.get("/api/v1/message-threads/?filter=UNREAD").json()["count"] == 0
    assert auth_client.get(url, HTTP_IF_NONE_MATCH=etag).status_code == 200

    newer = send_message(thread=db_thread, sender=physician, body="¿Algo más?")
    polled = auth_client.get(url, {"after": str(reply.pk)}).json()["results"]
    assert [item["id"] for item in polled] == [str(newer.pk)]
    assert auth_client.get(url, {"after": "not-a-uuid"}).status_code == 400
    assert auth_client.get(url, {"after": str(thread["id"])}).status_code == 400

    assert auth_client.get("/api/v1/message-threads/?search=algo").json()["count"] == 1
    assert auth_client.get("/api/v1/message-threads/?search=vance").json()["count"] == 1
    assert auth_client.get("/api/v1/message-threads/?search=zzz").json()["count"] == 0
    assert auth_client.get("/api/v1/message-threads/?filter=NOPE").status_code == 400
    detail = auth_client.get(f"/api/v1/message-threads/{thread['id']}/").json()
    assert detail["safety_notice"]
    # Bodies are encrypted at rest.
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT body FROM messaging_message LIMIT 1")
        assert "Claro" not in cursor.fetchone()[0]


def test_message_validation_and_idempotency(
    auth_client: APIClient, care_patient: User, physician: User
) -> None:
    thread = open_thread(auth_client, physician).json()
    url = f"/api/v1/message-threads/{thread['id']}/messages/"
    assert auth_client.post(url, {"body": "   "}, format="json").status_code == 400
    assert auth_client.post(url, {"body": "x" * 4001}, format="json").status_code == 400
    key = idem()
    first = auth_client.post(url, {"body": "Una vez"}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    second = auth_client.post(url, {"body": "Una vez"}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert first.json()["id"] == second.json()["id"]
    assert Message.objects.filter(thread_id=thread["id"]).count() == 1
    bad_read = auth_client.post(
        f"/api/v1/message-threads/{thread['id']}/read/",
        {"up_to_message_id": str(thread["id"])},
        format="json",
    )
    assert bad_read.status_code == 400


def test_thread_privacy(
    auth_client: APIClient, admin_client: APIClient, physician: User, staff_client: APIClient
) -> None:
    other = PatientFactory()
    make_evaluation(other, "IN_REVIEW", physician=physician)
    from apps.messaging.models import MessageThread

    thread = MessageThread.objects.create(patient=other, participant=physician)
    send_message(thread=thread, sender=physician, body="Privado")
    assert auth_client.get(f"/api/v1/message-threads/{thread.pk}/").status_code == 404
    assert auth_client.get(f"/api/v1/message-threads/{thread.pk}/messages/").status_code == 404
    assert staff_client.get("/api/v1/message-threads/").status_code == 403
    admin_messages = admin_client.get(f"/api/v1/message-threads/{thread.pk}/messages/")
    assert admin_messages.status_code == 200
    assert admin_messages.json()["results"][0]["is_mine"] is False
    assert admin_client.get("/api/v1/message-threads/?filter=UNREAD").json()["count"] == 1
    assert AuditLog.objects.filter(action="message_thread.read").exists()
    assert (
        admin_client.post(
            f"/api/v1/message-threads/{thread.pk}/messages/", {"body": "x"}, format="json"
        ).status_code
        == 403
    )


# --- Notifications ------------------------------------------------------------------


def test_notifications_api(auth_client: APIClient, patient_user: User) -> None:
    first = notify(patient_user, "ORDER_STATUS_CHANGED", target_kind="order", target_id=None)
    notify(patient_user, "NEW_MESSAGE")
    body = auth_client.get("/api/v1/notifications/").json()
    assert len(body["results"]) == 2
    item = body["results"][1]
    assert item["type"] == "ORDER_STATUS_CHANGED"
    assert item["title"] == "Tu pedido fue actualizado"
    assert item["target"] == {"kind": "order", "id": None}
    assert body["results"][0]["target"] is None
    assert auth_client.get("/api/v1/notifications/unread-count/").json() == {"count": 2}
    assert auth_client.post(f"/api/v1/notifications/{first.pk}/read/").status_code == 204
    assert auth_client.get("/api/v1/notifications/?unread=true").json()["results"][0]["type"] == (
        "NEW_MESSAGE"
    )
    assert len(auth_client.get("/api/v1/notifications/?unread=false").json()["results"]) == 1
    assert auth_client.get("/api/v1/notifications/?unread=maybe").status_code == 400
    assert auth_client.post("/api/v1/notifications/read-all/").status_code == 204
    assert auth_client.get("/api/v1/notifications/unread-count/").json() == {"count": 0}
    other = notify(PatientFactory(), "NEW_MESSAGE")
    assert auth_client.post(f"/api/v1/notifications/{other.pk}/read/").status_code == 404


def test_notifications_for_any_role(staff_client: APIClient) -> None:
    assert staff_client.get("/api/v1/notifications/").status_code == 200


def test_notification_email_preferences(patient_user: User) -> None:
    profile, _ = PatientProfile.objects.get_or_create(user=patient_user)
    profile.language = "en"
    profile.email_orders = False
    profile.save()
    with pytest.MonkeyPatch.context() as patch:
        sent: list[str] = []
        patch.setattr(
            "apps.notifications.tasks.send_notification_email.delay",
            lambda notification_id: sent.append(notification_id),
        )
        from django.test import TestCase

        with TestCase.captureOnCommitCallbacks(execute=True):
            order_note = notify(patient_user, "ORDER_STATUS_CHANGED")
            eval_note = notify(patient_user, "EVALUATION_STATUS_CHANGED")
        assert sent == [str(eval_note.pk)]
    assert order_note.title == "Your order has been updated"


def test_notification_email_task(patient_user: User) -> None:
    from apps.notifications.tasks import send_notification_email

    note = notify(patient_user, "LABS_REQUESTED", email=False)
    send_notification_email(str(note.pk))
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == note.title
    assert mail.outbox[0].to == [patient_user.email]
    send_notification_email("00000000-0000-0000-0000-000000000000")
    assert len(mail.outbox) == 1


def test_evaluation_transitions_notify_patient(patient_user: User, physician: User) -> None:
    _, evaluation = make_evaluation(patient_user, "SUBMITTED", physician=physician)
    assert evaluation is not None
    EvaluationService.transition(evaluation, "IN_REVIEW", actor=physician)
    EvaluationService.transition(evaluation, "PENDING_LABS", actor=physician)
    types = list(
        Notification.objects.filter(user=patient_user)
        .order_by("created_at")
        .values_list("type", flat=True)
    )
    assert types == ["EVALUATION_STATUS_CHANGED", "LABS_REQUESTED"]
    for note in Notification.objects.all():
        assert "Semaglutide" not in note.title + note.body


# --- Overview -----------------------------------------------------------------------


def overview(client: APIClient) -> dict[str, Any]:
    response = client.get("/api/v1/patients/me/overview/")
    assert response.status_code == 200, response.json()
    return response.json()


def sections(body: dict[str, Any]) -> dict[str, bool]:
    return {item["key"]: item["enabled"] for item in body["sections"]}


def test_overview_new_patient(auth_client: APIClient, patient_user: User) -> None:
    body = overview(auth_client)
    assert body["display_name"] == patient_user.full_name.split(" ")[0]
    assert sections(body) == {
        "OVERVIEW": True,
        "ASSESSMENT": True,
        "CONSULTATIONS": False,
        "TREATMENT": False,
        "ORDERS": False,
        "MESSAGES": False,
    }
    assert body["assessment"] is None
    assert body["health_summary"] is None
    assert body["primary_action"] == {"type": "NONE", "label": "", "target_id": None}
    assert body["next_consultation"] is None and body["next_dose"] is None
    assert body["active_orders_count"] == 0


def test_overview_draft_and_payment(auth_client: APIClient, patient_user: User) -> None:
    draft, _ = make_evaluation(patient_user, assessment_status="DRAFT", with_measurement=True)
    body = overview(auth_client)
    assert body["primary_action"]["type"] == "RESUME_ASSESSMENT"
    assert body["primary_action"]["target_id"] == str(draft.pk)
    assert body["assessment"]["status"] == "DRAFT"
    assert body["assessment"]["current_step_key"] is not None
    summary = body["health_summary"]
    assert summary["headline"].startswith("Based on your answers")
    assert summary["subheadline"] == "Your projected progress over the next 3 months."
    assert summary["current_weight"]["kg"] == 82.0
    assert summary["projection"]["months"] == 3
    draft.status = "CANCELLED"
    draft.save()
    awaiting, _ = make_evaluation(patient_user, "AWAITING_PAYMENT")
    body = overview(auth_client)
    assert body["primary_action"]["type"] == "PAY_FEE"
    assert body["primary_action"]["target_id"] == str(awaiting.pk)


def test_overview_labs_and_appointment(
    auth_client: APIClient, patient_user: User, physician: User
) -> None:
    assessment, _ = make_evaluation(
        patient_user, "SUBMITTED", physician=physician, labs_status="NOT_STARTED"
    )
    body = overview(auth_client)
    assert body["primary_action"]["type"] == "UPLOAD_LABS"
    assert sections(body)["CONSULTATIONS"] is True
    assert sections(body)["MESSAGES"] is True
    assessment.labs_status = "SUBMITTED"
    assessment.save()
    assert overview(auth_client)["primary_action"] == {
        "type": "SET_APPOINTMENT",
        "label": "Set appointment",
        "target_id": None,
    }
    consultation = make_consultation(patient_user, physician, start_in=timedelta(days=2))
    body = overview(auth_client)
    assert body["primary_action"]["type"] == "NONE"
    assert body["next_consultation"]["id"] == str(consultation.pk)
    assert body["next_consultation"]["physician"]["name"] == "Dr. Marcus Vance, MD"


def test_overview_approved(auth_client: APIClient, patient_user: User, physician: User) -> None:
    _, evaluation = make_evaluation(patient_user, "APPROVED", physician=physician)
    treatment = make_treatment(patient_user, physician, evaluation=evaluation)
    body = overview(auth_client)
    assert sections(body)["TREATMENT"] is True and sections(body)["ORDERS"] is True
    assert body["primary_action"] == {
        "type": "ORDER_MEDICINE",
        "label": "Order medicine",
        "target_id": str(treatment.pk),
    }
    assert body["next_dose"]["treatment_id"] == str(treatment.pk)
    assert body["next_dose"]["due_at"] is not None
    Order.objects.create(
        user=patient_user,
        treatment=treatment,
        package=package(),
        number="ADL-ORD-0500",
        status="COMPOUNDING",
        placed_at=timezone.now(),
        subtotal="1",
        shipping="0",
        tax="0",
        total="1",
    )
    notify(patient_user, "ORDER_STATUS_CHANGED")
    body = overview(auth_client)
    assert body["primary_action"]["type"] == "VIEW_TREATMENT"
    assert body["active_orders_count"] == 1
    assert body["unread_notifications_count"] == 1


def test_overview_is_patient_only(staff_client: APIClient, auth_client: APIClient) -> None:
    assert staff_client.get("/api/v1/patients/me/overview/").status_code == 403
    assert APIClient().get("/api/v1/patients/me/overview/").status_code == 401
