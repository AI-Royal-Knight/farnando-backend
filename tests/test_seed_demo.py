"""The demo seeder must be idempotent and cover every role."""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.accounts.models import ConsentRecord, User
from apps.catalog.models import FAQ, PublicStats, SiteSettings

pytestmark = pytest.mark.django_db


def test_seed_demo_creates_roles_and_is_idempotent() -> None:
    call_command("seed_demo", verbosity=0)
    call_command("seed_demo", verbosity=0)

    assert User.objects.count() == 5
    assert User.objects.filter(role="ADMIN", is_superuser=True).count() == 1
    assert User.objects.filter(role="PHYSICIAN").count() == 1
    assert User.objects.filter(role="STAFF").count() == 2
    assert User.objects.filter(role="PATIENT").count() == 1
    assert ConsentRecord.objects.count() == 16
    assert User.objects.get(email="marcus.vance@adelgaceya.pe").full_name == "Marcus Vance"
    assert User.objects.get(email="paciente@adelgaceya.pe").is_email_verified is True

    site = SiteSettings.load()
    assert site.contact_email == "contacto@adelgaceya.pe"
    assert site.contact_phone == "+51 1 555 0100"
    assert site.address == "Av. Larco 1234, Miraflores, Lima 15074, Perú"
    assert site.legal_entity_name == "Adelgaceya"
    assert FAQ.objects.count() == 5
    assert PublicStats.load().consultations_delivered == 50000


def test_seed_demo_creates_care_team() -> None:
    from apps.consultations.models import AvailabilityRule, ClinicianProfile
    from apps.patients.models import Address, PatientProfile

    call_command("seed_demo", verbosity=0)
    call_command("seed_demo", verbosity=0)

    marcus = ClinicianProfile.objects.get(user__email="marcus.vance@adelgaceya.pe")
    assert marcus.display_name == "Dr. Marcus Vance, MD"
    assert marcus.license_label == "CMP 12345"
    nurse = ClinicianProfile.objects.get(user__email="sarah.nurse@adelgaceya.pe")
    assert nurse.care_role == "NURSE"
    assert AvailabilityRule.objects.filter(physician=marcus.user).count() == 10
    profile = PatientProfile.objects.get(user__email="paciente@adelgaceya.pe")
    assert profile.assigned_nurse == nurse.user
    assert Address.objects.filter(user=profile.user).count() == 1
