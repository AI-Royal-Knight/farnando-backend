"""Part 4 infrastructure: beat job, staff alerts, admin, PDF writer, lazy profile."""

from __future__ import annotations

import pytest
from django.core import mail
from django.test import Client, override_settings

from apps.accounts.models import User
from apps.clinical.models import PhysicianInvitation
from apps.clinical.pdf import simple_pdf
from apps.clinical.permissions import physician_profile
from apps.clinical.tasks import run_clinical_jobs, send_staff_alert
from tests.factories.users import PhysicianFactory

pytestmark = pytest.mark.django_db


def test_clinical_beat_job_runs() -> None:
    assert run_clinical_jobs() == {"released_claims": 0, "applied_versions": 0}


@override_settings(SUPPORT_NOTIFICATION_EMAILS=["ops@adelgaceya.pe"])
def test_staff_alert_email_has_no_phi() -> None:
    assert send_staff_alert("physician_pending_verification") == 1
    assert mail.outbox[0].to == ["ops@adelgaceya.pe"]
    assert "credential verification" in mail.outbox[0].body


def test_simple_pdf_multi_page() -> None:
    data = simple_pdf([f"Line {index} (é)" for index in range(120)])
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert data.count(b"/Type /Page ") == 3


def test_physician_profile_created_lazily() -> None:
    physician = PhysicianFactory(full_name="Lazy Doc")
    profile = physician_profile(physician)
    assert profile.verification_status == "PENDING"
    assert profile.display_name == "Lazy Doc"
    assert physician_profile(physician).pk == profile.pk


def test_admin_creates_invitation_and_lists_clinical_models(admin_user: User) -> None:
    client = Client()
    client.force_login(admin_user)
    response = client.post(
        "/admin/clinical/physicianinvitation/add/",
        {"email": "invitee@clinic.pe", "status": "PENDING"},
        follow=True,
    )
    assert response.status_code == 200
    invitation = PhysicianInvitation.objects.get(email="invitee@clinic.pe")
    assert len(invitation.token_hash) == 64
    assert "shown once" in response.content.decode()
    for path in (
        "clinical/specialty",
        "clinical/physicianinvitation",
        "clinical/prescriptiondocument",
        "clinical/avatarupload",
        "clinical/clinicalnote",
        "consultations/clinicianprofile",
    ):
        assert client.get(f"/admin/{path}/").status_code == 200, path
