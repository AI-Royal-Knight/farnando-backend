"""Factories for identity tests."""

from __future__ import annotations

from typing import Any

import factory
from django.utils import timezone

from apps.accounts.enums import Role
from apps.accounts.models import ConsentRecord, User

DEFAULT_PASSWORD = "Str0ng-Passw0rd!2026"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    first_name = factory.Sequence(lambda n: f"Nombre{n}")
    last_name = "Apellido"
    role = Role.PATIENT
    is_email_verified = True
    email_verified_at = factory.LazyFunction(timezone.now)
    is_active = True

    @classmethod
    def _create(cls, model_class: type[User], *args: Any, **kwargs: Any) -> User:
        password = kwargs.pop("password", DEFAULT_PASSWORD)
        user = model_class(*args, **kwargs)
        user.set_password(password)
        user.save()
        return user


class PatientFactory(UserFactory):
    role = Role.PATIENT


class PhysicianFactory(UserFactory):
    role = Role.PHYSICIAN


class StaffFactory(UserFactory):
    role = Role.STAFF
    is_staff = True


class AdminFactory(UserFactory):
    role = Role.ADMIN
    is_staff = True
    is_superuser = True


class UnverifiedUserFactory(UserFactory):
    is_email_verified = False
    email_verified_at = None


class ConsentRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ConsentRecord

    user = factory.SubFactory(PatientFactory)
    document_type = ConsentRecord.DocumentType.TERMS
    version = "2026-01"
    ip_address = "127.0.0.1"
    user_agent = "pytest"
