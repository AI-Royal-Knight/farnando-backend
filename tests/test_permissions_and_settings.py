"""Role permission primitives and security configuration invariants."""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from django.conf import settings

from apps.core.permissions import (
    IsAdmin,
    IsClinicalStaff,
    IsEmailVerified,
    IsOwner,
    IsPatient,
    IsPhysician,
    IsStaff,
)
from tests.factories.users import PatientFactory, PhysicianFactory, StaffFactory


def _request(user: object) -> SimpleNamespace:
    return SimpleNamespace(user=user)


def test_role_permission_classes(db: object) -> None:
    patient = PatientFactory()
    physician = PhysicianFactory()
    staff = StaffFactory()

    assert IsPatient().has_permission(_request(patient), None) is True
    assert IsPatient().has_permission(_request(physician), None) is False
    assert IsPhysician().has_permission(_request(physician), None) is True
    assert IsStaff().has_permission(_request(staff), None) is True
    assert IsClinicalStaff().has_permission(_request(physician), None) is True
    assert IsAdmin().has_permission(_request(staff), None) is False
    assert IsEmailVerified().has_permission(_request(patient), None) is True

    unverified = PatientFactory(is_email_verified=False)
    assert IsEmailVerified().has_permission(_request(unverified), None) is False


def test_owner_permission(db: object) -> None:
    patient = PatientFactory()
    other = PatientFactory()
    owner = IsOwner()
    assert owner.has_object_permission(_request(patient), None, SimpleNamespace(user=patient))
    assert not owner.has_object_permission(_request(other), None, SimpleNamespace(user=patient))


def test_security_settings_invariants() -> None:
    base = importlib.import_module("config.settings.base")
    assert base.MIDDLEWARE[0] == "django.middleware.security.SecurityMiddleware"
    assert base.MIDDLEWARE[1] == "whitenoise.middleware.WhiteNoiseMiddleware"
    assert base.STORAGES["staticfiles"]["BACKEND"] == (
        "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )
    assert base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    assert base.X_FRAME_OPTIONS == "DENY"
    assert base.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert base.SESSION_COOKIE_HTTPONLY is True
    assert base.CSRF_COOKIE_SAMESITE == "Lax"
    assert "bearerAuth" in base.SPECTACULAR_SETTINGS["APPEND_COMPONENTS"]["securitySchemes"]
    assert base.SPECTACULAR_SETTINGS["SERVE_INCLUDE_SCHEMA"] is False
    assert base.SPECTACULAR_SETTINGS["COMPONENT_SPLIT_REQUEST"] is True


def test_test_settings_do_not_use_redis_or_real_email() -> None:
    backend = settings.CACHES["default"]["BACKEND"]
    assert "locmem" in backend or "dummy" in backend
    assert settings.EMAIL_BACKEND.endswith("locmem.EmailBackend")
    assert settings.AXES_ENABLED is False
