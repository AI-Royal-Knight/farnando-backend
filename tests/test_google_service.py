"""Google social login service tests using a fake allauth provider."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from apps.accounts.models import User
from apps.accounts.services import GoogleLoginError, GoogleLoginService
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db


def _login(existing_user: User | None, *, verified: bool = True, account_pk: object = object()):
    user = existing_user
    if user is None:
        user = User(email="nuevo-google@example.com", first_name="Nuevo")
    return SimpleNamespace(
        is_existing=existing_user is not None,
        user=user,
        email_addresses=[SimpleNamespace(verified=verified)],
        account=SimpleNamespace(
            pk=account_pk if existing_user is not None else None, extra_data={}
        ),
        save=Mock(),
        lookup=Mock(),
    )


def test_google_existing_user_is_logged_in_without_role_change() -> None:
    user = PatientFactory(email="google@example.com", is_email_verified=False)
    login = _login(user)
    adapter = Mock()
    adapter.get_provider.return_value.verify_token.return_value = login

    with patch("apps.accounts.services.get_social_adapter", return_value=adapter):
        result = GoogleLoginService.login(request=SimpleNamespace(META={}), id_token="credential")

    assert result.user.pk == user.pk
    assert result.is_new_user is False
    user.refresh_from_db()
    assert user.is_email_verified is True
    assert user.role == "PATIENT"


def test_google_new_user_is_created_as_patient() -> None:
    login = _login(None)
    adapter = Mock()
    adapter.get_provider.return_value.verify_token.return_value = login

    with patch("apps.accounts.services.get_social_adapter", return_value=adapter):
        result = GoogleLoginService.login(
            request=SimpleNamespace(META={}),
            id_token="credential",
            consents=[
                {"document_type": "TERMS_OF_SERVICE", "version": "2026-11-01"},
                {"document_type": "PRIVACY_POLICY", "version": "2026-11-01"},
            ],
        )

    user = User.objects.get(email="nuevo-google@example.com")
    assert result.user.pk == user.pk
    assert result.is_new_user is True
    assert user.role == "PATIENT"
    assert user.is_email_verified is True
    assert user.has_usable_password() is False
    login.save.assert_called_once()


def test_google_unverified_email_is_rejected() -> None:
    login = _login(None, verified=False)
    adapter = Mock()
    adapter.get_provider.return_value.verify_token.return_value = login

    with (
        patch("apps.accounts.services.get_social_adapter", return_value=adapter),
        pytest.raises(GoogleLoginError),
    ):
        GoogleLoginService.login(request=SimpleNamespace(META={}), id_token="credential")
    assert not User.objects.filter(email="nuevo-google@example.com").exists()


def test_google_provider_errors_are_sanitised() -> None:
    adapter = Mock()
    adapter.get_provider.side_effect = RuntimeError("secret provider detail")
    with (
        patch("apps.accounts.services.get_social_adapter", return_value=adapter),
        pytest.raises(GoogleLoginError) as exc,
    ):
        GoogleLoginService.login(request=SimpleNamespace(META={}), id_token="credential")
    assert "secret provider detail" not in str(exc.value)


@override_settings(GOOGLE_CLIENT_ID="")
def test_google_requires_configuration() -> None:
    with pytest.raises(GoogleLoginError):
        GoogleLoginService.login(request=SimpleNamespace(META={}), id_token="credential")
