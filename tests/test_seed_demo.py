"""The demo seeder must be idempotent and cover every role."""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.accounts.models import ConsentRecord, User

pytestmark = pytest.mark.django_db


def test_seed_demo_creates_roles_and_is_idempotent() -> None:
    call_command("seed_demo", verbosity=0)
    call_command("seed_demo", verbosity=0)

    assert User.objects.count() == 4
    assert User.objects.filter(role="ADMIN", is_superuser=True).count() == 1
    assert User.objects.filter(role="PHYSICIAN").count() == 1
    assert User.objects.filter(role="STAFF").count() == 1
    assert User.objects.filter(role="PATIENT").count() == 1
    assert ConsentRecord.objects.count() == 12
    assert User.objects.get(email="marcus.vance@adelgaceya.pe").first_name == "Marcus"
    assert User.objects.get(email="paciente@adelgaceya.pe").is_email_verified is True
