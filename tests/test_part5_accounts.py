"""Part 5 section 2: staff accounts, staff invitations and physician management."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Evaluation
from apps.clinical.models import PhysicianInvitation, Specialty
from apps.consultations.models import ClinicianProfile
from apps.core.models import AuditLog
from apps.notifications.models import Notification
from apps.staff.models import StaffInvitation, StaffProfile
from tests.factories.part3 import make_consultation, make_evaluation, make_physician
from tests.factories.part5 import make_staff, token_from_outbox
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db

STAFF = "/api/v1/admin/staff/"
CONSENTS = [
    {"document_type": "TERMS_OF_SERVICE", "version": "2026-11-01"},
    {"document_type": "PRIVACY_POLICY", "version": "2026-11-01"},
]


def invite(admin_client: APIClient, **overrides: Any) -> Any:
    body = {"full_name": "Sofia Lopez, RN", "email": "sofia@adelgaceya.pe", "staff_type": "NURSE"}
    body.update(overrides)
    return admin_client.post(STAFF, body, format="json")


def test_staff_endpoints_are_admin_only(staff_client: APIClient, auth_client: APIClient) -> None:
    assert staff_client.get(STAFF).status_code == 403
    assert auth_client.get(STAFF).status_code == 403
    assert APIClient().get(STAFF).status_code == 401


def test_invite_accept_and_login_flow(
    admin_client: APIClient, api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        response = invite(admin_client, phone="+51987654321")
    assert response.status_code == 201, response.json()
    member = response.json()
    assert member["status"] == "INVITED"
    assert member["staff_type"] == "NURSE"
    assert "CLINICAL_READ" in member["permissions"]
    assert member["phone"] == "+51987654321"
    assert "password" not in member
    user = User.objects.get(pk=member["id"])
    assert user.is_active is False and not user.has_usable_password()

    duplicate = invite(admin_client)
    assert duplicate.status_code == 400

    token = token_from_outbox("staff")
    lookup = api_client.get(f"/api/v1/public/staff-invitations/{token}/")
    assert lookup.status_code == 200
    assert lookup.json()["email"] == "sofia@adelgaceya.pe"
    assert api_client.get("/api/v1/public/staff-invitations/nope/").status_code == 404

    mismatch = api_client.post(
        f"/api/v1/public/staff-invitations/{token}/accept/",
        {"password": "Str0ng-Passw0rd!2026", "confirm_password": "other", "consents": CONSENTS},
        format="json",
    )
    assert mismatch.status_code == 400
    accepted = api_client.post(
        f"/api/v1/public/staff-invitations/{token}/accept/",
        {
            "password": "Str0ng-Passw0rd!2026",
            "confirm_password": "Str0ng-Passw0rd!2026",
            "consents": CONSENTS,
        },
        format="json",
    )
    assert accepted.status_code == 201, accepted.json()
    assert accepted.json()["mfa_enrollment_required"] is True
    assert accepted.json()["tokens"]["access"]
    user.refresh_from_db()
    assert user.is_active and user.staff_profile.status == "ACTIVE"
    reused = api_client.post(
        f"/api/v1/public/staff-invitations/{token}/accept/",
        {
            "password": "Str0ng-Passw0rd!2026",
            "confirm_password": "Str0ng-Passw0rd!2026",
            "consents": CONSENTS,
        },
        format="json",
    )
    assert reused.status_code in (404, 409, 410)


def test_expired_invitation_returns_410(admin_client: APIClient, api_client: APIClient) -> None:
    with_token: dict[str, str] = {}
    invite(admin_client)
    invitation = StaffInvitation.objects.get()
    from apps.clinical.onboarding import hash_token

    with_token["token"] = "expired-token-value"
    invitation.token_hash = hash_token(with_token["token"])
    invitation.expires_at = timezone.now() - timezone.timedelta(minutes=1)
    invitation.save()
    response = api_client.get(f"/api/v1/public/staff-invitations/{with_token['token']}/")
    assert response.status_code == 410
    assert response.json()["code"] == "invitation_expired"


def test_list_update_suspend_reactivate_and_delete(
    admin_client: APIClient, admin_user: User, make_auth_client: Callable[[User], APIClient]
) -> None:
    member = make_staff("SUPPORT_AGENT", name="Lucia Ramos")
    invited = invite(admin_client).json()

    listing = admin_client.get(STAFF).json()
    assert listing["count"] == 2
    assert admin_client.get(f"{STAFF}?status=INVITED").json()["count"] == 1
    assert admin_client.get(f"{STAFF}?staff_type=SUPPORT_AGENT&search=lucia").json()["count"] == 1
    assert admin_client.get(f"{STAFF}?status=NOPE").status_code == 400
    assert admin_client.get(f"{STAFF}{member.pk}/").json()["full_name"] == "Lucia Ramos"

    no_reason = admin_client.patch(
        f"{STAFF}{member.pk}/", {"permissions": ["PATIENTS_READ"]}, format="json"
    )
    assert no_reason.status_code == 400
    updated = admin_client.patch(
        f"{STAFF}{member.pk}/",
        {"permissions": ["PATIENTS_READ", "ORDERS_READ"], "reason": "Moved to fulfilment"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.json()["permissions"] == ["ORDERS_READ", "PATIENTS_READ"]
    assert AuditLog.objects.filter(action_type="PERMISSION_CHANGE").exists()

    staff_client = make_auth_client(member)
    assert staff_client.get("/api/v1/staff/orders/").status_code == 200
    suspended = admin_client.post(
        f"{STAFF}{member.pk}/suspend/", {"reason": "Left the company"}, format="json"
    )
    assert suspended.status_code == 200 and suspended.json()["status"] == "SUSPENDED"
    member.refresh_from_db()
    assert member.is_active is False
    assert staff_client.get("/api/v1/staff/orders/").status_code == 401
    reactivated = admin_client.post(f"{STAFF}{member.pk}/reactivate/")
    assert reactivated.json()["status"] == "ACTIVE"

    active_delete = admin_client.delete(f"{STAFF}{member.pk}/")
    assert active_delete.status_code == 409
    resend = admin_client.post(f"{STAFF}{invited['id']}/resend-invitation/")
    assert resend.status_code == 200
    assert admin_client.delete(f"{STAFF}{invited['id']}/").status_code == 204
    assert StaffInvitation.objects.get().status == "REVOKED"
    assert admin_client.get(f"{STAFF}{invited['id']}/").status_code == 404


def test_staff_permission_denied_details(make_auth_client: Callable[[User], APIClient]) -> None:
    nurse = make_staff("NURSE")
    client = make_auth_client(nurse)
    response = client.get("/api/v1/staff/orders/")
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert response.json()["details"]["required"] == "ORDERS_READ"
    bare = make_auth_client(make_staff("SUPPORT_AGENT", [], name="Bare"))
    assert bare.get("/api/v1/staff/patients/").json()["details"]["required"] == "PATIENTS_READ"
    assert make_auth_client(PatientFactory()).get("/api/v1/staff/dashboard/").status_code == 403


def test_physician_invitations(
    admin_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    url = "/api/v1/admin/physician-invitations/"
    with django_capture_on_commit_callbacks(execute=True):
        created = admin_client.post(url, {"email": "doc@clinic.pe"}, format="json")
    assert created.status_code == 201
    assert created.json()["status"] == "PENDING"
    token_from_outbox("physicians")
    assert admin_client.post(url, {"email": "doc@clinic.pe"}, format="json").status_code == 409
    invitation_id = created.json()["id"]
    assert admin_client.get(f"{url}?status=PENDING").json()["count"] == 1
    assert admin_client.post(f"{url}{invitation_id}/resend/").status_code == 200
    assert admin_client.post(f"{url}{invitation_id}/revoke/").json()["status"] == "REVOKED"
    assert admin_client.post(f"{url}{invitation_id}/resend/").status_code == 409
    assert PhysicianInvitation.objects.get().status == "REVOKED"


def test_physician_verification_suspension_and_credentials(admin_client: APIClient) -> None:
    pending = make_physician(name="Ana Ruiz", verification_status="PENDING")
    other = make_physician(name="Carlos Vega")
    base = f"/api/v1/admin/physicians/{pending.pk}/"

    cards = admin_client.get("/api/v1/admin/physicians/").json()
    assert cards["count"] == 2
    card = next(row for row in cards["results"] if row["id"] == str(pending.pk))
    assert card["verification_status"] == "PENDING"
    assert set(card["stats"]) == {
        "assigned_evaluations_count",
        "active_patients_count",
        "consultations_this_month",
    }
    assert admin_client.get("/api/v1/admin/physicians/?status=INVITED").json()["count"] == 0
    assert admin_client.get("/api/v1/admin/physicians/?status=PENDING").json()["count"] == 1

    unchecked = admin_client.post(f"{base}verify/", {"checked_registry": False}, format="json")
    assert unchecked.status_code == 400
    verified = admin_client.post(
        f"{base}verify/", {"checked_registry": True, "note": "CMP ok"}, format="json"
    )
    assert verified.status_code == 200, verified.json()
    assert verified.json()["verification_status"] == "VERIFIED"
    assert Notification.objects.filter(user=pending, type="CREDENTIALS_VERIFIED").exists()
    again = admin_client.post(f"{base}verify/", {"checked_registry": True}, format="json")
    assert again.status_code == 409

    patient = PatientFactory()
    _, evaluation = make_evaluation(patient, "IN_REVIEW", physician=pending)
    assert evaluation is not None
    consultation = make_consultation(patient, pending, start_in=timezone.timedelta(days=2))
    suspended = admin_client.post(
        f"{base}suspend/",
        {"reason": "License under review", "reassign_to_physician_id": str(other.pk)},
        format="json",
    )
    assert suspended.status_code == 200, suspended.json()
    detail = suspended.json()
    assert detail["verification_status"] == "SUSPENDED"
    assert detail["account_status"] == "SUSPENDED"
    evaluation = Evaluation.objects.get(pk=evaluation.pk)
    assert evaluation.status == "SUBMITTED" and evaluation.physician_id is None
    assert evaluation.assigned_physician_id == other.pk
    consultation.refresh_from_db()
    assert consultation.needs_staff_attention is True

    reactivated = admin_client.post(f"{base}reactivate/")
    assert reactivated.json()["verification_status"] == "VERIFIED"

    no_reason = admin_client.patch(
        f"{base}credentials/", {"license_number": "99887"}, format="json"
    )
    assert no_reason.status_code == 400
    specialty = Specialty.objects.get(name="Endocrinology")
    changed = admin_client.patch(
        f"{base}credentials/",
        {
            "license_number": "99887",
            "specialty_id": str(specialty.pk),
            "specialty_registration_number": "RNE123",
            "reason": "Registry correction",
        },
        format="json",
    )
    assert changed.status_code == 200, changed.json()
    assert ClinicianProfile.objects.get(user=pending).license_number == "99887"

    rejected_doc = make_physician(name="Rita Paz", verification_status="PENDING")
    rejected = admin_client.post(
        f"/api/v1/admin/physicians/{rejected_doc.pk}/reject/",
        {"reason": "License not found"},
        format="json",
    )
    assert rejected.json()["verification_status"] == "REJECTED"


def test_staff_profile_missing_means_no_permissions(
    staff_client: APIClient, staff_user: User
) -> None:
    assert not StaffProfile.objects.filter(user=staff_user).exists()
    response = staff_client.get("/api/v1/staff/dashboard/")
    assert response.status_code == 200
    assert response.json()["stats"] == {
        "unassigned_evaluations_count": None,
        "open_tickets_count": None,
        "orders_processing_count": None,
        "avg_first_response_minutes": None,
    }


def test_invited_staff_activated_in_django_admin_gets_permissions(
    admin_client: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    """Setting a password and ticking "active" in Django admin finishes the invitation."""

    member = invite(admin_client, staff_type="SUPPORT_AGENT").json()
    user = User.objects.get(pk=member["id"])
    user.is_active = True
    user.is_email_verified = True
    user.save()
    assert StaffProfile.objects.get(user=user).status == "INVITED"  # no password yet
    client = make_auth_client(user)
    assert client.get("/api/v1/staff/tickets/").status_code == 403
    user.set_password("Str0ng-Passw0rd!2026")
    user.save()

    assert StaffProfile.objects.get(user=user).status == "ACTIVE"
    assert StaffInvitation.objects.get(user=user).status == "ACCEPTED"
    assert AuditLog.objects.filter(action="staff.activated_without_invitation").count() == 1
    assert client.get("/api/v1/staff/tickets/").status_code == 200
