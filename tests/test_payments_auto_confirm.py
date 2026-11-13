"""Local-only auto-confirmation of fake-provider charges, and its production guard."""

from __future__ import annotations

import importlib
import uuid

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from rest_framework.test import APIClient

from apps.assessments.enums import EvaluationStatus, FeeStatus, PaymentStatus
from apps.assessments.models import Evaluation, Payment
from tests.test_assessments_part2 import submitted_assessment

pytestmark = pytest.mark.django_db

FAKE = "apps.assessments.payments.FakePaymentProvider"


def _pay(client: APIClient, assessment_id: object) -> None:
    response = client.post(
        f"/api/v1/assessments/{assessment_id}/fee/pay/",
        {"payment_method": "CULQI", "provider_token": "tok_test"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 200, response.json()


@override_settings(PAYMENTS_AUTO_CONFIRM=True, PAYMENT_PROVIDER=FAKE)
def test_fake_fee_payment_is_settled_after_commit(
    auth_client: APIClient, patient_user: object, django_capture_on_commit_callbacks: object
) -> None:
    assessment, _ = submitted_assessment(auth_client, patient_user)
    with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
        _pay(auth_client, assessment.id)

    payment = Payment.objects.get(evaluation__assessment=assessment)
    assert payment.status == PaymentStatus.SUCCEEDED
    evaluation = Evaluation.objects.get(assessment=assessment)
    assert evaluation.status == EvaluationStatus.SUBMITTED
    assert evaluation.fee.status == FeeStatus.PAID


@override_settings(PAYMENTS_AUTO_CONFIRM=False, PAYMENT_PROVIDER=FAKE)
def test_without_the_flag_payment_waits_for_the_webhook(
    auth_client: APIClient, patient_user: object, django_capture_on_commit_callbacks: object
) -> None:
    assessment, _ = submitted_assessment(auth_client, patient_user)
    with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
        _pay(auth_client, assessment.id)

    assert Payment.objects.get(evaluation__assessment=assessment).status == PaymentStatus.PENDING
    assert Evaluation.objects.get(assessment=assessment).status == EvaluationStatus.AWAITING_PAYMENT


def _production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "DJANGO_SECRET_KEY": "production-check-only-long-random-secret-0123456789",
        "DJANGO_ALLOWED_HOSTS": "api.example.com",
        "DATABASE_URL": "postgres://u:p@localhost:5432/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "CELERY_BROKER_URL": "redis://localhost:6379/1",
        "AWS_STORAGE_BUCKET_NAME": "bucket",
        "FIELD_ENCRYPTION_KEYS": "tW8uBgbY93Ih64CaqRsEIDheNMVimjTE97KQKAyVAxA=",
        "CSRF_TRUSTED_ORIGINS": "https://app.example.com",
        "EMAIL_BACKEND": "anymail.backends.mailgun.EmailBackend",
        "CAPTCHA_VERIFIER": "apps.core.captcha.TurnstileCaptchaVerifier",
        "CAPTCHA_SECRET_KEY": "secret",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _load_production() -> None:
    # Re-evaluate base with the patched environment, then production on top of it.
    import sys

    import config.settings.base as base

    importlib.reload(base)
    sys.modules.pop("config.settings.production", None)
    importlib.import_module("config.settings.production")


def test_production_rejects_the_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("PAYMENT_PROVIDER", FAKE)
    with pytest.raises(ImproperlyConfigured, match="PAYMENT_PROVIDER"):
        _load_production()


def test_production_rejects_auto_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    _production_env(monkeypatch)
    monkeypatch.setenv("PAYMENT_PROVIDER", "apps.assessments.payments.CulqiPaymentProvider")
    monkeypatch.setenv("PAYMENTS_AUTO_CONFIRM", "True")
    with pytest.raises(ImproperlyConfigured, match="PAYMENTS_AUTO_CONFIRM"):
        _load_production()
