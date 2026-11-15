"""Public physician sign-up: a PHYSICIAN account whose license waits for admin verification."""

from __future__ import annotations

from typing import Any

import pytest
from django.core import mail
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import ConsentRecord, User
from apps.clinical.models import Specialty
from apps.consultations.models import ClinicianProfile
from tests.factories.users import UserFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/auth/register-physician/"
PASSWORD = "Str0ng-Passw0rd!2026"
CONSENTS = [
    {"document_type": "TERMS_OF_SERVICE", "version": "2026-11-01"},
    {"document_type": "PRIVACY_POLICY", "version": "2026-11-01"},
    {"document_type": "CLINICIAN_AGREEMENT", "version": "2026-11-01"},
]


@pytest.fixture(autouse=True)
def _no_throttle() -> None:
    cache.clear()  # the auth scope is throttled per IP


def body(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "full_name": "Dr. Lucia Paredes",
        "email": "Lucia@Clinic.pe",
        "license_number": "cmp-77881",
        "specialty_id": str(Specialty.objects.get(name="Endocrinology").pk),
        "password": PASSWORD,
        "confirm_password": PASSWORD,
        "consents": CONSENTS,
    }
    data.update(overrides)
    return data


def test_creates_pending_physician(
    api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(URL, body(), format="json")
    assert response.status_code == 201, response.json()
    assert response.json() == {"status": "pending_review"}

    user = User.objects.get(email="lucia@clinic.pe")
    assert user.role == "PHYSICIAN"
    assert user.is_email_verified is False
    assert user.check_password(PASSWORD)
    profile = ClinicianProfile.objects.get(user=user)
    assert profile.verification_status == "PENDING"
    assert profile.license_number == "CMP-77881"
    assert profile.specialty == "Endocrinology"
    # The registration number is optional at sign-up; the admin checks it when verifying.
    assert profile.specialty_registration_number == ""
    kinds = set(ConsentRecord.objects.filter(user=user).values_list("document_type", flat=True))
    assert kinds == {"TERMS_OF_SERVICE", "PRIVACY_POLICY", "CLINICIAN_AGREEMENT"}
    assert any("lucia@clinic.pe" in message.to for message in mail.outbox)


def test_rejects_bad_input(api_client: APIClient) -> None:
    no_agreement = api_client.post(URL, body(consents=CONSENTS[:2]), format="json")
    assert no_agreement.status_code == 400 and no_agreement.json()["code"] == "consent_required"
    mismatch = api_client.post(URL, body(confirm_password="Other-Passw0rd!2026"), format="json")
    assert mismatch.status_code == 400
    weak = api_client.post(
        URL, body(password="password", confirm_password="password"), format="json"
    )
    assert weak.status_code == 400
    cache.clear()
    unknown = api_client.post(
        URL, body(specialty_id="00000000-0000-0000-0000-000000000000"), format="json"
    )
    assert unknown.status_code == 400 and "specialty_id" in unknown.json()["details"]
    assert not User.objects.filter(email="lucia@clinic.pe").exists()


def test_duplicate_email_and_license(api_client: APIClient) -> None:
    UserFactory(email="taken@clinic.pe")
    taken = api_client.post(URL, body(email="taken@clinic.pe"), format="json")
    assert taken.status_code == 400 and "email" in taken.json()["details"]

    assert api_client.post(URL, body(), format="json").status_code == 201
    cache.clear()
    duplicate = api_client.post(
        URL, body(email="other@clinic.pe", license_number="CMP-77881"), format="json"
    )
    assert duplicate.status_code == 400 and "license_number" in duplicate.json()["details"]


def test_verified_email_then_login_reaches_the_pending_gate(api_client: APIClient) -> None:
    assert api_client.post(URL, body(), format="json").status_code == 201
    user = User.objects.get(email="lucia@clinic.pe")
    user.is_email_verified = True
    user.save(update_fields=["is_email_verified"])

    cache.clear()
    login = APIClient().post(
        "/api/v1/auth/login/", {"email": "lucia@clinic.pe", "password": PASSWORD}, format="json"
    )
    assert login.status_code == 200, login.json()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['tokens']['access']}")
    assert client.get("/api/v1/physicians/me/").json()["verification_status"] == "PENDING"
    gated = client.get("/api/v1/physicians/me/dashboard/")
    assert gated.status_code == 403 and gated.json()["code"] == "credentials_not_verified"
