"""Admin forms, user manager and model helper coverage."""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite

from apps.accounts.admin import ConsentRecordAdmin, EmailUserCreationForm, UserAdmin
from apps.accounts.models import ConsentRecord, User

pytestmark = pytest.mark.django_db


def test_email_user_creation_form_creates_usable_password() -> None:
    form = EmailUserCreationForm(
        data={
            "email": "Admin.Created@Example.com",
            "first_name": "Ada",
            "last_name": "Admin",
            "role": "STAFF",
            "password1": "A-very-l0ng-password!",
            "password2": "A-very-l0ng-password!",
        }
    )
    assert form.is_valid(), form.errors
    user = form.save()
    assert user.email == "admin.created@example.com"
    assert user.check_password("A-very-l0ng-password!")


def test_email_user_creation_form_rejects_mismatch() -> None:
    form = EmailUserCreationForm(
        data={
            "email": "a@example.com",
            "role": "PATIENT",
            "password1": "A-very-l0ng-password!",
            "password2": "different-password",
        }
    )
    assert not form.is_valid()
    assert "password2" in form.errors


def test_user_manager_rejects_empty_email() -> None:
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="A-very-l0ng-password!")


def test_superuser_manager_sets_admin_role() -> None:
    user = User.objects.create_superuser(email="root@example.com", password="A-very-l0ng-password!")
    assert user.role == "ADMIN"
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.is_email_verified is True


def test_user_helpers(patient_user: User, physician_user: User) -> None:
    patient_user.first_name = ""
    patient_user.last_name = ""
    assert patient_user.get_full_name() == patient_user.email
    assert patient_user.get_short_name() == patient_user.email
    assert patient_user.is_patient is True
    assert physician_user.is_physician is True


def test_consent_str() -> None:
    record = ConsentRecord.objects.create(
        user=User.objects.create_user(email="c@example.com", password="A-very-l0ng-password!"),
        document_type=ConsentRecord.DocumentType.TERMS,
        version="2026-01",
    )
    assert str(record).startswith(f"{record.user_id}:TERMS")


def test_immutable_consent_admin(staff_user: User) -> None:
    admin = ConsentRecordAdmin(ConsentRecord, AdminSite())
    assert admin.has_add_permission(None) is False
    assert admin.has_change_permission(None) is False
    assert admin.has_delete_permission(None) is False
    user_admin = UserAdmin(User, AdminSite())
    assert user_admin.model is User
