"""Part 3 infrastructure: UTC rendering, provider placeholders, beat tasks, admin."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory, override_settings
from rest_framework import serializers
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.payments import CulqiPaymentProvider
from apps.consultations.video import get_video_provider
from apps.core.exceptions import ServiceUnavailable
from apps.payments.models import WebhookEvent
from tests.factories.part3 import give_consents, make_consultation, make_physician

pytestmark = pytest.mark.django_db


def test_datetimes_render_in_utc_with_z() -> None:
    value = datetime(2026, 11, 12, 13, 0, tzinfo=UTC)
    assert serializers.DateTimeField().to_representation(value) == "2026-11-12T13:00:00Z"
    parsed = serializers.DateTimeField().to_internal_value("2026-11-12T13:00:00Z")
    assert parsed == value


def test_api_timestamps_are_utc(auth_client: APIClient, patient_user: User) -> None:
    created = auth_client.get("/api/v1/auth/me/").json()["created_at"]
    assert created == patient_user.date_joined.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@override_settings(VIDEO_PROVIDER="apps.consultations.video.DailyVideoProvider")
def test_daily_provider_placeholder(auth_client: APIClient, patient_user: User) -> None:
    physician = make_physician()
    give_consents(patient_user)
    consultation = make_consultation(patient_user, physician, start_in=timedelta(minutes=1))
    response = auth_client.post(f"/api/v1/consultations/{consultation.pk}/join/")
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"


def test_culqi_provider_placeholder() -> None:
    provider = CulqiPaymentProvider()
    with pytest.raises(ServiceUnavailable):
        provider.create_card(provider_token="x", customer_ref="y")
    with pytest.raises(ServiceUnavailable):
        provider.refund(
            provider_payment_id="x", amount=Decimal("1"), currency="PEN", idempotency_key="k"
        )


def test_video_provider_import_path() -> None:
    with override_settings(VIDEO_PROVIDER="apps.consultations.video.FakeVideoProvider"):
        assert get_video_provider().name == "DAILY"


def test_beat_tasks_run() -> None:
    from apps.consultations.tasks import run_consultation_jobs
    from apps.orders.tasks import run_subscription_jobs

    assert run_consultation_jobs() == {"expired": 0, "no_show": 0, "reminders": 0}
    assert run_subscription_jobs() == {"renewed": 0, "retried": 0, "cancelled": 0, "resumed": 0}


def test_webhook_events_are_read_only_in_admin(admin_user: User) -> None:
    request = RequestFactory().get("/admin/")
    request.user = admin_user
    model_admin = site._registry[WebhookEvent]
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False


def test_admin_changelists_load(admin_user: User) -> None:
    from django.test import Client

    client = Client()
    client.force_login(admin_user)
    for path in (
        "patients/patientprofile",
        "patients/address",
        "patients/ubigeo",
        "consultations/clinicianprofile",
        "consultations/consultation",
        "treatments/treatment",
        "orders/order",
        "orders/package",
        "orders/subscription",
        "payments/webhookevent",
        "messaging/messagethread",
        "notifications/notification",
    ):
        assert client.get(f"/admin/{path}/").status_code == 200, path
