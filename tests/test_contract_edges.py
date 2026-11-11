"""Cross-cutting contract edge cases."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from allauth.socialaccount.models import SocialAccount
from django.test import Client
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.permissions import HasCurrentConsents, IsEmailVerified
from apps.accounts.serializers import UserSummarySerializer
from apps.core.exceptions import ConsentRequired, EmailNotVerified

pytestmark = pytest.mark.django_db

VERSION = "2026-11-01"


def test_refresh_invalid_token_code(client: Client) -> None:
    response = client.post(
        "/api/v1/auth/token/refresh/", {"refresh": "not-a-token"}, content_type="application/json"
    )
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


def test_expired_access_token_code(patient_user, client: Client) -> None:
    token = AccessToken.for_user(patient_user)
    token.set_exp(lifetime=-timedelta(minutes=1))
    response = client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


def test_refresh_unknown_field_is_rejected(client: Client) -> None:
    response = client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": "x", "unexpected": True},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_field"


def test_malformed_bearer_header_is_token_invalid(client: Client) -> None:
    response = client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION="Bearer")
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


def test_login_unknown_field_is_rejected(client: Client) -> None:
    response = client.post(
        "/api/v1/auth/login/",
        {"email": "a@example.com", "password": "x", "remember_me": True},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_field"


def test_user_summary_contains_contract_fields(patient_user) -> None:
    payload = UserSummarySerializer(patient_user).data
    assert set(payload) == {
        "id",
        "email",
        "full_name",
        "role",
        "is_email_verified",
        "auth_providers",
        "assessment_status",
        "mfa_enabled",
        "created_at",
    }
    assert payload["assessment_status"] is None
    assert payload["mfa_enabled"] is False
    assert payload["auth_providers"] == ["PASSWORD"]


def test_user_summary_lists_both_auth_providers(patient_user) -> None:
    SocialAccount.objects.create(user=patient_user, provider="google", uid="google-123")
    payload = UserSummarySerializer(patient_user).data
    assert payload["auth_providers"] == ["PASSWORD", "GOOGLE"]


def test_is_email_verified_permission_raises_contract_code() -> None:
    unverified = SimpleNamespace(is_authenticated=True, is_email_verified=False)
    request = SimpleNamespace(user=unverified)
    with pytest.raises(EmailNotVerified):
        IsEmailVerified().has_permission(request, None)


def test_has_current_consents_permission(patient_user) -> None:
    request = SimpleNamespace(user=patient_user)
    with pytest.raises(ConsentRequired):
        HasCurrentConsents().has_permission(request, None)

    from apps.accounts.models import ConsentRecord

    for document_type in (
        ConsentRecord.DocumentType.TERMS_OF_SERVICE,
        ConsentRecord.DocumentType.PRIVACY_POLICY,
    ):
        ConsentRecord.objects.create(
            user=patient_user, document_type=document_type, version=VERSION
        )
    assert HasCurrentConsents().has_permission(request, None) is True


def test_spanish_locale_for_framework_validation(client: Client) -> None:
    response = client.post(
        "/api/v1/auth/login/",
        {"email": "a@example.com"},
        content_type="application/json",
        HTTP_ACCEPT_LANGUAGE="es",
    )
    assert response.status_code == 400
    assert "requerido" in str(response.json()["details"]).lower()
    assert response.json()["message"] == "La validación falló."
