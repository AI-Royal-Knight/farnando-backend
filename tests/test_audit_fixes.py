"""Regression tests for the full-backend audit (deviations 135-137)."""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Payment, Program
from apps.backoffice.privacy import anonymise
from apps.backoffice.reports import _mrr
from apps.core.images import ImageRejected, sanitize_image
from apps.orders.models import Subscription
from apps.payments.models import SavedPaymentMethod
from apps.staff.models import Permission
from tests.factories.part3 import idem, make_evaluation, make_physician, make_treatment, package
from tests.factories.part5 import make_staff

pytestmark = pytest.mark.django_db


# --- Staff need CLINICAL_READ for assessments --------------------------------------


def test_staff_assessment_reads_need_clinical_read(
    patient_user: User, make_auth_client: Callable[[User], APIClient]
) -> None:
    assessment, _ = make_evaluation(patient_user, "SUBMITTED")
    detail = f"/api/v1/assessments/{assessment.pk}/"

    without = make_auth_client(make_staff("SUPPORT_AGENT", [Permission.TICKETS_MANAGE]))
    assert without.get(detail).status_code == 404
    assert without.get("/api/v1/assessments/").json()["count"] == 0
    assert without.get(f"{detail}evaluation/").status_code == 404

    clinical = make_auth_client(
        make_staff("NURSE", [Permission.CLINICAL_READ], name="Clara Clinical")
    )
    assert clinical.get(detail).status_code == 200
    assert clinical.get("/api/v1/assessments/").json()["count"] == 1


# --- Refund limit fails closed across currencies -----------------------------------


def test_refund_in_other_currency_needs_admin(
    patient_user: User,
    make_auth_client: Callable[[User], APIClient],
    admin_client: APIClient,
) -> None:
    payment = Payment.objects.create(
        user=patient_user,
        purpose="EVALUATION_FEE",
        status="SUCCEEDED",
        amount=Decimal("49.00"),
        currency="USD",
        provider_payment_id="chr_usd_1",
    )
    ops = make_auth_client(make_staff("OPERATIONS", name="Olga Ops"))
    url = f"/api/v1/staff/payments/{payment.pk}/refund/"
    small = {"reason_code": "CUSTOMER_REQUEST", "amount": {"amount": "1.00", "currency": "USD"}}
    blocked = ops.post(url, small, format="json", HTTP_IDEMPOTENCY_KEY=idem())
    assert blocked.status_code == 403
    assert blocked.json()["details"]["required"] == "ADMIN"
    allowed = admin_client.post(url, small, format="json", HTTP_IDEMPOTENCY_KEY=idem())
    assert allowed.status_code == 202, allowed.json()


# --- Privacy deletion ----------------------------------------------------------------


def test_anonymise_stops_charges_and_drops_identities(patient_user: User) -> None:
    doctor = make_physician()
    _, evaluation = make_evaluation(patient_user, "APPROVED", physician=doctor)
    treatment = make_treatment(patient_user, doctor, evaluation=evaluation)
    card = SavedPaymentMethod.objects.create(
        user=patient_user,
        provider_card_id="crd_1",
        brand="Visa",
        last4="4242",
        exp_month=12,
        exp_year=2030,
        is_default=True,
    )
    subscription = Subscription.objects.create(
        user=patient_user,
        treatment=treatment,
        package=package("Custom 3-Month Protocol"),
        payment_method=card,
        status="ACTIVE",
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
        next_billing_at=timezone.now() + timedelta(days=30),
        charge_per_cycle=Decimal("567.00"),
    )
    EmailAddress.objects.create(user=patient_user, email=patient_user.email, verified=True)
    SocialAccount.objects.create(user=patient_user, provider="google", uid="g-123")

    anonymise(patient_user)

    subscription.refresh_from_db()
    card.refresh_from_db()
    assert subscription.status == "CANCELLED" and subscription.next_billing_at is None
    assert card.is_deleted is True
    assert not EmailAddress.objects.filter(user=patient_user).exists()
    assert not SocialAccount.objects.filter(user=patient_user).exists()


def test_inactive_user_cannot_finish_mfa_login(
    api_client: APIClient, patient_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    patient_user.is_active = False
    patient_user.save(update_fields=["is_active"])
    monkeypatch.setattr("apps.accounts.mfa.verify_challenge", lambda token, code: patient_user)
    response = api_client.post(
        "/api/v1/auth/mfa/verify/", {"mfa_token": "t", "code": "123456"}, format="json"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "account_disabled"


# --- Settings and reports ------------------------------------------------------------


def test_new_program_uses_reapply_cooldown_setting(admin_client: APIClient) -> None:
    from apps.treatments.models import Product
    from tests.test_part5_catalog import program_body

    admin_client.put(
        "/api/v1/admin/settings/evaluation/",
        {
            "estimated_review_hours": {"min": 24, "max": 48},
            "claim_timeout_hours": 4,
            "urgent_after_hours": 24,
            "reapply_cooldown_days": 45,
            "reason": "Longer cooldown",
        },
        format="json",
    )
    product = Product.objects.get(code="semaglutide-0-5")
    created = admin_client.post(
        "/api/v1/admin/programs/", program_body(str(product.pk)), format="json"
    )
    assert created.status_code == 201, created.json()
    assert Program.objects.get(pk=created.json()["id"]).not_suitable_cooldown_days == 45


def test_mrr_is_measured_at_period_end(patient_user: User) -> None:
    doctor = make_physician()
    _, evaluation = make_evaluation(patient_user, "APPROVED", physician=doctor)
    treatment = make_treatment(patient_user, doctor, evaluation=evaluation)
    plan = package("Custom 3-Month Protocol")
    now = timezone.now()
    common = {
        "user": patient_user,
        "treatment": treatment,
        "package": plan,
        "current_period_start": now,
        "current_period_end": now + timedelta(days=30),
        "charge_per_cycle": Decimal("567.00"),
    }
    created_late = Subscription.objects.create(status="ACTIVE", **common)
    cancelled_after = Subscription.objects.create(status="CANCELLED", cancelled_at=now, **common)
    Subscription.objects.filter(pk=cancelled_after.pk).update(created_at=now - timedelta(days=60))
    Subscription.objects.filter(pk=created_late.pk).update(created_at=now - timedelta(days=1))
    one = _mrr(now + timedelta(days=1))  # only the still-active one
    assert one > 0
    # Ten days ago only the later-cancelled subscription was active.
    assert _mrr(now - timedelta(days=10)) == one
    # Before either existed, nothing counts.
    assert _mrr(now - timedelta(days=90)) == 0
    Subscription.objects.filter(pk=cancelled_after.pk).update(cancelled_at=now - timedelta(days=20))
    assert _mrr(now - timedelta(days=10)) == 0


# --- Image sanitising ------------------------------------------------------------------


def _jpeg_with_orientation(size: tuple[int, int], orientation: int) -> bytes:
    image = Image.new("RGB", size, (10, 20, 30))
    exif = image.getexif()
    exif[0x0112] = orientation
    exif[0x010F] = "SecretCam"
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def test_sanitize_applies_orientation_and_strips_exif() -> None:
    clean, content_type = sanitize_image(_jpeg_with_orientation((40, 20), 6), max_pixels=100)
    assert content_type == "image/jpeg"
    result = Image.open(io.BytesIO(clean))
    assert result.size == (20, 40)
    assert not result.getexif()


def test_sanitize_rejects_oversized_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = _jpeg_with_orientation((300, 50), 1)

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("pixels were decoded")

    monkeypatch.setattr(Image.Image, "load", fail)
    with pytest.raises(ImageRejected, match="at most 100 px"):
        sanitize_image(raw, max_pixels=100)
