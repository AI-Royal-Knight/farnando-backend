"""Contract Part 4 section 2: invitations, specialties, avatars, MFA and profile."""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings
from django.utils import timezone
from PIL import Image, PngImagePlugin
from rest_framework.test import APIClient

from apps.accounts.mfa import current_step, totp_at
from apps.accounts.models import MfaDevice, User
from apps.clinical.models import AvatarUpload, PhysicianInvitation, Specialty
from apps.clinical.onboarding import create_invitation
from apps.consultations.models import ClinicianProfile
from apps.notifications.models import Notification
from tests.factories.part3 import make_physician
from tests.factories.users import DEFAULT_PASSWORD, PhysicianFactory

pytestmark = pytest.mark.django_db

CONSENTS = [
    {"document_type": "TERMS_OF_SERVICE", "version": "2026-11-01"},
    {"document_type": "PRIVACY_POLICY", "version": "2026-11-01"},
    {"document_type": "CLINICIAN_AGREEMENT", "version": "2026-11-01"},
]


def accept_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "full_name": "Dr. Sofia Morales, MD",
        "license_number": "54321",
        "specialty_id": str(Specialty.objects.get(name="General Medicine").pk),
        "phone": "+51987650001",
        "password": "Str0ng-Passw0rd!2026",
        "confirm_password": "Str0ng-Passw0rd!2026",
        "consents": CONSENTS,
    }
    body.update(overrides)
    return body


def png_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "secret-metadata")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=info)
    return buffer.getvalue()


# --- Invitations -------------------------------------------------------------------


def test_specialties(api_client: APIClient) -> None:
    body = api_client.get("/api/v1/public/specialties/").json()
    names = {item["name"]: item["requires_registration_number"] for item in body}
    assert names["Endocrinology"] is True and names["General Medicine"] is False


def test_invitation_lookup_states(api_client: APIClient, admin_user: User) -> None:
    invitation, token = create_invitation(email="Sofia@Clinic.pe", invited_by=admin_user)
    body = api_client.get(f"/api/v1/public/physician-invitations/{token}/").json()
    assert body["email"] == "sofia@clinic.pe"
    assert body["status"] == "PENDING"
    assert body["invited_by_name"] == admin_user.full_name
    assert api_client.get("/api/v1/public/physician-invitations/nope/").status_code == 404

    invitation.status = "REVOKED"
    invitation.save()
    revoked = api_client.get(f"/api/v1/public/physician-invitations/{token}/")
    assert revoked.status_code == 410 and revoked.json()["code"] == "invitation_revoked"
    invitation.status = "PENDING"
    invitation.expires_at = timezone.now() - timedelta(minutes=1)
    invitation.save()
    expired = api_client.get(f"/api/v1/public/physician-invitations/{token}/")
    assert expired.status_code == 410 and expired.json()["code"] == "invitation_expired"


def test_accept_invitation(api_client: APIClient) -> None:
    _invitation, token = create_invitation(email="sofia@clinic.pe")
    url = f"/api/v1/public/physician-invitations/{token}/accept/"
    no_agreement = api_client.post(url, accept_body(consents=CONSENTS[:2]), format="json")
    assert no_agreement.status_code == 400 and no_agreement.json()["code"] == "consent_required"
    mismatch = api_client.post(url, accept_body(confirm_password="other"), format="json")
    assert mismatch.status_code == 400
    needs_rne = api_client.post(
        url,
        accept_body(specialty_id=str(Specialty.objects.get(name="Endocrinology").pk)),
        format="json",
    )
    assert needs_rne.status_code == 400

    cache.clear()  # the accept endpoint is throttled to 5/hour per IP
    response = api_client.post(url, accept_body(), format="json")
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["user"]["role"] == "PHYSICIAN"
    assert body["user"]["is_email_verified"] is True
    assert "access" in body["tokens"]
    assert body["mfa_enrollment_required"] is False
    profile = ClinicianProfile.objects.get(user__email="sofia@clinic.pe")
    assert profile.verification_status == "PENDING"
    assert profile.license_number == "54321"
    assert profile.license_label == "CMP 54321"
    assert PhysicianInvitation.objects.get().status == "ACCEPTED"
    again = api_client.post(url, accept_body(), format="json")
    assert again.status_code == 410 and again.json()["code"] == "invitation_already_used"

    _second, token2 = create_invitation(email="other@clinic.pe")
    duplicate = api_client.post(
        f"/api/v1/public/physician-invitations/{token2}/accept/", accept_body(), format="json"
    )
    assert duplicate.status_code == 400 and "license_number" in duplicate.json()["details"]


def test_pending_physician_profile_and_gate(api_client: APIClient) -> None:
    _invitation, token = create_invitation(email="new@clinic.pe")
    tokens = api_client.post(
        f"/api/v1/public/physician-invitations/{token}/accept/", accept_body(), format="json"
    ).json()["tokens"]
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    me = client.get("/api/v1/physicians/me/").json()
    assert me["verification_status"] == "PENDING"
    assert me["full_name"] == "Dr. Sofia Morales, MD"
    assert me["specialty"]["name"] == "General Medicine"
    assert me["accepting_new_consultations"] is True
    gated = client.get("/api/v1/physicians/me/dashboard/")
    assert gated.status_code == 403 and gated.json()["code"] == "credentials_not_verified"
    assert client.get("/api/v1/physicians/me/availability-rules/").status_code == 200

    patched = client.patch(
        "/api/v1/physicians/me/",
        {
            "headline": "Metabolic care",
            "bio": "Hi",
            "languages": ["es", "en"],
            "timezone": "America/Bogota",
        },
        format="json",
    )
    assert patched.status_code == 200
    assert patched.json()["languages"] == ["es", "en"]
    read_only = client.patch("/api/v1/physicians/me/", {"license_number": "9"}, format="json")
    assert read_only.status_code == 400 and read_only.json()["code"] == "read_only_field"
    unknown = client.patch("/api/v1/physicians/me/", {"nickname": "x"}, format="json")
    assert unknown.json()["code"] == "unknown_field"
    assert (
        client.patch("/api/v1/physicians/me/", {"headline": "x" * 121}, format="json").status_code
        == 400
    )

    profile = ClinicianProfile.objects.get(user__email="new@clinic.pe")
    profile.verification_status = "VERIFIED"
    profile.save()
    from apps.clinical.onboarding import notify_verification_change

    notify_verification_change(profile)
    assert Notification.objects.filter(type="CREDENTIALS_VERIFIED").exists()
    assert client.get("/api/v1/physicians/me/dashboard/").status_code == 200


def test_patients_do_not_see_unverified_physicians(auth_client: APIClient) -> None:
    make_physician(name="Verified")
    make_physician(name="Pending", verification_status="PENDING")
    assert auth_client.get("/api/v1/physicians/").json()["count"] == 1


def test_profile_is_physician_only(staff_client: APIClient) -> None:
    assert staff_client.get("/api/v1/physicians/me/").status_code == 403


# --- Avatars ---------------------------------------------------------------------------


def test_avatar_upload_anonymous_with_invitation(api_client: APIClient) -> None:
    _invitation, token = create_invitation(email="avatar@clinic.pe")
    assert (
        api_client.post(
            "/api/v1/avatar-uploads/",
            {"content_type": "image/png", "size_bytes": 10},
            format="json",
        ).status_code
        == 401
    )
    created = api_client.post(
        "/api/v1/avatar-uploads/",
        {"content_type": "image/png", "size_bytes": 1000, "invitation_token": token},
        format="json",
    )
    assert created.status_code == 201, created.json()
    upload = AvatarUpload.objects.get(pk=created.json()["id"])
    default_storage.save(upload.storage_key, ContentFile(png_bytes()))
    confirmed = api_client.post(
        f"/api/v1/avatar-uploads/{upload.pk}/confirm/", {"invitation_token": token}, format="json"
    )
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed.json()["status"] == "READY"
    with default_storage.open(upload.storage_key, "rb") as handle:
        assert b"secret-metadata" not in handle.read()

    accepted = api_client.post(
        f"/api/v1/public/physician-invitations/{token}/accept/",
        accept_body(license_number="77777", avatar_upload_id=str(upload.pk)),
        format="json",
    )
    assert accepted.status_code == 201, accepted.json()
    assert (
        ClinicianProfile.objects.get(user__email="avatar@clinic.pe").avatar_key
        == upload.storage_key
    )


def test_avatar_rejects_bad_files(make_auth_client: Callable[[User], APIClient]) -> None:
    client = make_auth_client(make_physician())
    too_big = client.post(
        "/api/v1/avatar-uploads/",
        {"content_type": "image/png", "size_bytes": 6_000_000},
        format="json",
    )
    assert too_big.status_code == 413
    created = client.post(
        "/api/v1/avatar-uploads/", {"content_type": "image/png", "size_bytes": 100}, format="json"
    ).json()
    upload = AvatarUpload.objects.get(pk=created["id"])
    default_storage.save(upload.storage_key, ContentFile(b"not an image at all"))
    bad = client.post(f"/api/v1/avatar-uploads/{upload.pk}/confirm/", {}, format="json")
    assert bad.status_code == 400
    with override_settings(AVATAR_MAX_PIXELS=32):
        created = client.post(
            "/api/v1/avatar-uploads/",
            {"content_type": "image/png", "size_bytes": 100},
            format="json",
        ).json()
        upload = AvatarUpload.objects.get(pk=created["id"])
        default_storage.save(upload.storage_key, ContentFile(png_bytes()))
        assert (
            client.post(
                f"/api/v1/avatar-uploads/{upload.pk}/confirm/", {}, format="json"
            ).status_code
            == 400
        )


def test_avatar_patch_on_profile(make_auth_client: Callable[[User], APIClient]) -> None:
    physician = make_physician()
    client = make_auth_client(physician)
    created = client.post(
        "/api/v1/avatar-uploads/", {"content_type": "image/png", "size_bytes": 100}, format="json"
    ).json()
    upload = AvatarUpload.objects.get(pk=created["id"])
    default_storage.save(upload.storage_key, ContentFile(png_bytes()))
    client.post(f"/api/v1/avatar-uploads/{upload.pk}/confirm/", {}, format="json")
    response = client.patch(
        "/api/v1/physicians/me/", {"avatar_upload_id": str(upload.pk)}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["avatar_url"]


# --- MFA -------------------------------------------------------------------------------


def _enroll(client: APIClient) -> tuple[str, list[str]]:
    setup = client.post("/api/v1/auth/mfa/totp/setup/")
    assert setup.status_code == 200, setup.json()
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/Adelgaceya")
    wrong = client.post("/api/v1/auth/mfa/totp/confirm/", {"code": "000000"}, format="json")
    assert wrong.status_code == 400
    confirm = client.post(
        "/api/v1/auth/mfa/totp/confirm/", {"code": totp_at(secret, current_step())}, format="json"
    )
    assert confirm.status_code == 200
    codes = confirm.json()["backup_codes"]
    assert len(codes) == 10
    return secret, codes


def _login(email: str) -> dict[str, Any]:
    cache.clear()  # the auth scope is throttled to 5/min
    response = APIClient().post(
        "/api/v1/auth/login/", {"email": email, "password": DEFAULT_PASSWORD}, format="json"
    )
    assert response.status_code == 200, response.json()
    return dict(response.json())


def test_mfa_enrollment_and_login(auth_client: APIClient, patient_user: User) -> None:
    secret, codes = _enroll(auth_client)
    assert auth_client.get("/api/v1/auth/me/").json()["mfa_enabled"] is True
    assert auth_client.post("/api/v1/auth/mfa/totp/setup/").status_code == 409

    challenge = _login(patient_user.email)
    assert challenge["mfa_required"] is True and challenge["methods"] == ["TOTP"]
    assert "tokens" not in challenge
    verified = APIClient().post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": challenge["mfa_token"], "code": codes[0]},
        format="json",
    )
    assert verified.status_code == 200
    assert "access" in verified.json()["tokens"]
    cache.clear()
    reused = APIClient().post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": challenge["mfa_token"], "code": codes[1]},
        format="json",
    )
    assert reused.status_code == 401

    second = _login(patient_user.email)
    cache.clear()
    used_backup = APIClient().post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": second["mfa_token"], "code": codes[0]},
        format="json",
    )
    assert used_backup.status_code == 400
    # Same TOTP step cannot be replayed after confirm used it; use the next step.
    MfaDevice.objects.filter(user=patient_user).update(last_used_step=None)
    cache.clear()
    totp = APIClient().post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": second["mfa_token"], "code": totp_at(secret, current_step())},
        format="json",
    )
    assert totp.status_code == 200

    MfaDevice.objects.filter(user=patient_user).update(last_used_step=None)
    cache.clear()
    disabled = auth_client.post(
        "/api/v1/auth/mfa/totp/disable/",
        {"password": DEFAULT_PASSWORD, "code": totp_at(secret, current_step())},
        format="json",
    )
    assert disabled.status_code == 204
    assert "tokens" in _login(patient_user.email)


def test_mfa_lockout_after_five_wrong_codes(auth_client: APIClient, patient_user: User) -> None:
    secret, _codes = _enroll(auth_client)
    challenge = _login(patient_user.email)
    for _ in range(5):
        cache.clear()
        wrong = APIClient().post(
            "/api/v1/auth/mfa/verify/",
            {"mfa_token": challenge["mfa_token"], "code": "123456"},
            format="json",
        )
        assert wrong.status_code == 400
    MfaDevice.objects.filter(user=patient_user).update(last_used_step=None)
    cache.clear()
    locked = APIClient().post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": challenge["mfa_token"], "code": totp_at(secret, current_step())},
        format="json",
    )
    assert locked.status_code == 401 and locked.json()["code"] == "token_invalid"


@override_settings(MFA_REQUIRED_ROLES=["PHYSICIAN", "STAFF", "ADMIN"])
def test_mandatory_mfa_for_clinical_roles(make_auth_client: Callable[[User], APIClient]) -> None:
    physician = make_physician()
    client = make_auth_client(physician)
    blocked = client.get("/api/v1/physicians/me/")
    assert blocked.status_code == 403 and blocked.json()["code"] == "mfa_enrollment_required"
    assert client.get("/api/v1/auth/me/").status_code == 200
    secret, _codes = _enroll(client)
    assert client.get("/api/v1/physicians/me/").status_code == 200
    MfaDevice.objects.filter(user=physician).update(last_used_step=None)
    refused = client.post(
        "/api/v1/auth/mfa/totp/disable/",
        {"password": DEFAULT_PASSWORD, "code": totp_at(secret, current_step())},
        format="json",
    )
    assert refused.status_code == 403


@override_settings(MFA_REQUIRED_ROLES=["PHYSICIAN"])
def test_accept_reports_mfa_enrollment(api_client: APIClient) -> None:
    _invitation, token = create_invitation(email="mfa@clinic.pe")
    body = api_client.post(
        f"/api/v1/public/physician-invitations/{token}/accept/",
        accept_body(license_number="11111"),
        format="json",
    ).json()
    assert body["mfa_enrollment_required"] is True
    assert PhysicianFactory._meta.model.objects.filter(email="mfa@clinic.pe").exists()
