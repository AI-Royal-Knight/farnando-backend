"""Contact form, complaints book and idempotency (contract 4.x/5.x)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.core import mail
from django.test import Client, override_settings
from django.utils import timezone

from apps.support.models import ComplaintBookEntry, ContactMessage

pytestmark = pytest.mark.django_db

CONTACT_URL = "/api/v1/public/contact-messages/"
COMPLAINTS_URL = "/api/v1/public/complaints/"


def contact_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "full_name": "Maria Gonzalez",
        "email": "maria@example.com",
        "message": "I would like more information about the programme.",
    }
    payload.update(overrides)
    return payload


def complaint_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "full_name": "Maria Gonzalez",
        "email": "maria@example.com",
        "category_code": "SERVICE",
        "filing_type": "RECLAMO",
        "description": "The consultation did not start at the scheduled time.",
    }
    payload.update(overrides)
    return payload


def key() -> str:
    return str(uuid.uuid4())


def test_contact_message_creates_record_and_sends_emails(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        override_settings(SUPPORT_NOTIFICATION_EMAILS=["staff@example.com"]),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = client.post(CONTACT_URL, contact_payload(), content_type="application/json")
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "NEW"
    assert payload["id"]
    assert ContactMessage.objects.count() == 1
    assert len(mail.outbox) == 2
    assert {message.to[0] for message in mail.outbox} == {"maria@example.com", "staff@example.com"}


def test_contact_message_strips_html_and_rejects_unknown_fields(client: Client) -> None:
    response = client.post(
        CONTACT_URL,
        contact_payload(message="<script>alert(1)</script>Hello there contact"),
        content_type="application/json",
    )
    assert response.status_code == 201
    assert "<script>" not in ContactMessage.objects.get().message

    unknown = client.post(
        CONTACT_URL, contact_payload(subject="hello"), content_type="application/json"
    )
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "unknown_field"


def test_contact_email_is_throttled(client: Client) -> None:
    for _ in range(3):
        response = client.post(CONTACT_URL, contact_payload(), content_type="application/json")
        assert response.status_code == 201
    throttled = client.post(CONTACT_URL, contact_payload(), content_type="application/json")
    assert throttled.status_code == 429
    assert throttled.json()["code"] == "throttled"


def test_complaint_creates_sequential_number_and_is_idempotent(client: Client) -> None:
    idem = key()
    first = client.post(
        COMPLAINTS_URL,
        complaint_payload(),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idem,
    )
    assert first.status_code == 201
    payload = first.json()
    assert payload["complaint_number"] == f"LR-{timezone.now().year}-000001"
    assert payload["status"] == "RECEIVED"
    due = timezone.datetime.fromisoformat(payload["response_due_at"].replace("Z", "+00:00"))
    created = timezone.datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00"))
    assert timedelta(hours=23) < due - created < timedelta(hours=25)

    replay = client.post(
        COMPLAINTS_URL,
        complaint_payload(),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idem,
    )
    assert replay.status_code == 201
    assert replay.json() == payload
    assert ComplaintBookEntry.objects.count() == 1

    conflict = client.post(
        COMPLAINTS_URL,
        complaint_payload(description="A different complaint description for the same key."),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idem,
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_complaint_number_increments(client: Client) -> None:
    first = client.post(
        COMPLAINTS_URL,
        complaint_payload(),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key(),
    )
    second = client.post(
        COMPLAINTS_URL,
        complaint_payload(),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key(),
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["complaint_number"].endswith("000001")
    assert second.json()["complaint_number"].endswith("000002")


def test_complaint_requires_idempotency_key(client: Client) -> None:
    response = client.post(COMPLAINTS_URL, complaint_payload(), content_type="application/json")
    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


def test_complaint_rejects_invalid_category_and_document(client: Client) -> None:
    invalid_category = client.post(
        COMPLAINTS_URL,
        complaint_payload(category_code="NOPE"),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key(),
    )
    assert invalid_category.status_code == 400
    assert invalid_category.json()["code"] == "validation_error"

    invalid_document = client.post(
        COMPLAINTS_URL,
        complaint_payload(document_type="DNI", document_number="123"),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key(),
    )
    assert invalid_document.status_code == 400
    assert "document_number" in invalid_document.json()["details"]


def test_complaint_status_lookup_requires_matching_email(client: Client) -> None:
    created = client.post(
        COMPLAINTS_URL,
        complaint_payload(),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key(),
    ).json()
    number = created["complaint_number"]

    ok = client.get(f"/api/v1/public/complaints/{number}/?email=maria@example.com")
    assert ok.status_code == 200
    assert ok.json()["complaint_number"] == number

    assert (
        client.get(f"/api/v1/public/complaints/{number}/?email=wrong@example.com").status_code
        == 404
    )
    assert client.get(f"/api/v1/public/complaints/{number}/").status_code == 404
    assert (
        client.get("/api/v1/public/complaints/LR-2026-999999/?email=maria@example.com").status_code
        == 404
    )


def test_complaint_sends_confirmation_to_complainant(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        override_settings(SUPPORT_NOTIFICATION_EMAILS=["staff@example.com"]),
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = client.post(
            COMPLAINTS_URL,
            complaint_payload(),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=key(),
        )
    assert response.status_code == 201
    recipients = {message.to[0] for message in mail.outbox}
    assert recipients == {"maria@example.com", "staff@example.com"}
