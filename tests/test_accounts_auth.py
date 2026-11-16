"""Contract Part 1 authentication and consent API tests."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from axes.models import AccessAttempt
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import ConsentRecord, User
from apps.accounts.tokens import make_email_verification_token
from tests.factories.users import DEFAULT_PASSWORD, PatientFactory, UnverifiedUserFactory

pytestmark = pytest.mark.django_db

CONSENT_VERSION = "2026-11-01"
REGISTER_URL = "/api/v1/auth/register/"
LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
VERIFY_URL = "/api/v1/auth/verify-email/"
RESEND_URL = "/api/v1/auth/verify-email/resend/"
RESET_URL = "/api/v1/auth/password-reset/"
RESET_CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"
ME_URL = "/api/v1/auth/me/"
CHANGE_PASSWORD_URL = "/api/v1/auth/change-password/"
CONSENTS_ME_URL = "/api/v1/consents/me/"
CONSENTS_URL = "/api/v1/consents/"
GOOGLE_URL = "/api/v1/auth/google/"


def consent_items() -> list[dict[str, str]]:
    return [
        {"document_type": "TERMS_OF_SERVICE", "version": CONSENT_VERSION},
        {"document_type": "PRIVACY_POLICY", "version": CONSENT_VERSION},
    ]


def registration_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "full_name": "Maria Gonzalez",
        "email": "nueva@example.com",
        "password": "A-very-l0ng-password!",
        "confirm_password": "A-very-l0ng-password!",
        "consents": consent_items(),
    }
    payload.update(overrides)
    return payload


def api_login(client: APIClient, user: User, password: str = DEFAULT_PASSWORD) -> Any:
    return client.post(LOGIN_URL, {"email": user.email, "password": password}, format="json")


def query_from_email(body: str, path_fragment: str) -> dict[str, str]:
    match = re.search(rf"https?://\S*{re.escape(path_fragment)}\?\S+", body)
    assert match, body
    return {key: values[0] for key, values in parse_qs(urlparse(match.group(0)).query).items()}


def test_registration_returns_tokens_and_records_consents(
    api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(REGISTER_URL, registration_payload(), format="json")

    assert response.status_code == 201
    payload = response.json()
    assert payload["user"]["full_name"] == "Maria Gonzalez"
    assert payload["user"]["role"] == "PATIENT"
    assert payload["user"]["auth_providers"] == ["PASSWORD"]
    assert payload["tokens"]["access"] and payload["tokens"]["refresh"]
    user = User.objects.get(email="nueva@example.com")
    assert user.full_name == "Maria Gonzalez"
    assert user.consents.count() == 2
    assert set(user.consents.values_list("document_type", flat=True)) == {
        "TERMS_OF_SERVICE",
        "PRIVACY_POLICY",
    }
    assert len(mail.outbox) == 1


def test_registration_duplicate_sends_email_but_is_generic(
    api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    PatientFactory(email="known@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            REGISTER_URL, registration_payload(email="known@example.com"), format="json"
        )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "Unable to create" in response.json()["details"]["email"][0]
    assert User.objects.filter(email="known@example.com").count() == 1
    assert len(mail.outbox) == 1
    assert "Ya existe" in mail.outbox[0].body


def test_registration_requires_current_consents_and_valid_password(api_client: APIClient) -> None:
    no_consents = api_client.post(REGISTER_URL, registration_payload(consents=[]), format="json")
    assert no_consents.status_code == 400
    assert no_consents.json()["code"] == "consent_required"

    wrong_version = api_client.post(
        REGISTER_URL,
        registration_payload(
            consents=[
                {"document_type": "TERMS_OF_SERVICE", "version": "2020-01-01"},
                {"document_type": "PRIVACY_POLICY", "version": CONSENT_VERSION},
            ]
        ),
        format="json",
    )
    assert wrong_version.status_code == 400
    assert wrong_version.json()["code"] == "consent_required"

    short = api_client.post(
        REGISTER_URL,
        registration_payload(password="short", confirm_password="short"),
        format="json",
    )
    assert short.status_code == 400
    assert "password" in short.json()["details"]


def test_unknown_fields_are_rejected(api_client: APIClient) -> None:
    response = api_client.post(REGISTER_URL, registration_payload(role="ADMIN"), format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_field"
    assert response.json()["details"] == {"role": ["Unknown field."]}


def test_login_returns_user_and_tokens(api_client: APIClient) -> None:
    user = UnverifiedUserFactory(email="unverified@example.com")
    response = api_login(api_client, user)

    assert response.status_code == 200
    data = response.json()
    assert set(data["tokens"]) == {"access", "refresh"}
    assert data["user"]["email"] == user.email
    assert data["user"]["is_email_verified"] is False
    assert data["user"]["created_at"].endswith("Z")


def test_login_errors_are_uniform(api_client: APIClient) -> None:
    PatientFactory(email="exists@example.com")
    wrong = api_client.post(
        LOGIN_URL, {"email": "exists@example.com", "password": "nope-nope"}, format="json"
    )
    unknown = api_client.post(
        LOGIN_URL, {"email": "missing@example.com", "password": "nope-nope"}, format="json"
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert wrong.json()["message"] == unknown.json()["message"]


def test_inactive_account_returns_403_account_disabled(api_client: APIClient) -> None:
    user = PatientFactory(is_active=False)
    response = api_login(api_client, user)
    assert response.status_code == 403
    assert response.json()["code"] == "account_disabled"


def test_verify_email_token_is_single_field_and_single_use(api_client: APIClient) -> None:
    user = UnverifiedUserFactory()
    token = make_email_verification_token(user)
    response = api_client.post(VERIFY_URL, {"token": token}, format="json")
    assert response.status_code == 200
    assert response.json()["user"]["is_email_verified"] is True

    reused = api_client.post(VERIFY_URL, {"token": token}, format="json")
    assert reused.status_code == 400
    assert reused.json()["code"] == "token_invalid"


def test_verify_email_rejects_invalid_token(api_client: APIClient) -> None:
    response = api_client.post(VERIFY_URL, {"token": "nope"}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "token_invalid"


def test_resend_verification(
    api_client: APIClient,
    unverified_user: User,
    make_auth_client: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    client = make_auth_client(unverified_user)
    unknown_field = client.post(RESEND_URL, {"unexpected": True}, format="json")
    assert unknown_field.status_code == 400
    assert unknown_field.json()["code"] == "unknown_field"

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(RESEND_URL, {}, format="json")
    assert response.status_code == 204
    assert len(mail.outbox) == 1

    verified = PatientFactory()
    with django_capture_on_commit_callbacks(execute=True):
        already = make_auth_client(verified).post(RESEND_URL, {}, format="json")
    assert already.status_code == 409
    assert already.json()["code"] == "conflict"


def test_password_reset_flow_and_token_revocation(
    api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    user = PatientFactory(email="reset@example.com")
    old_refresh = api_login(api_client, user).json()["tokens"]["refresh"]

    with django_capture_on_commit_callbacks(execute=True):
        request_response = api_client.post(RESET_URL, {"email": "reset@example.com"}, format="json")
    assert request_response.status_code == 204
    params = query_from_email(mail.outbox[0].body, "/reset-password")

    confirm = api_client.post(
        RESET_CONFIRM_URL,
        {
            "token": params["token"],
            "password": "N3w-Strong-Password!",
            "confirm_password": "N3w-Strong-Password!",
        },
        format="json",
    )
    assert confirm.status_code == 204
    user.refresh_from_db()
    assert user.check_password("N3w-Strong-Password!")
    assert api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json").status_code == 401


def test_password_reset_does_not_reveal_unknown_email(api_client: APIClient) -> None:
    PatientFactory(email="known-reset@example.com")
    known = api_client.post(RESET_URL, {"email": "known-reset@example.com"}, format="json")
    unknown = api_client.post(RESET_URL, {"email": "unknown@example.com"}, format="json")
    assert known.status_code == unknown.status_code == 204
    assert known.content == unknown.content


def test_refresh_rotation_and_blacklist(api_client: APIClient) -> None:
    user = PatientFactory()
    tokens = api_login(api_client, user).json()["tokens"]
    old_refresh = tokens["refresh"]
    rotated = api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json")
    assert rotated.status_code == 200
    assert set(rotated.json()) == {"access", "refresh"}
    assert rotated.json()["refresh"] != old_refresh
    assert api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json").status_code == 401


def test_logout_requires_auth_and_blacklists(
    api_client: APIClient, patient_user: User, make_auth_client: Any
) -> None:
    tokens = api_login(api_client, patient_user).json()["tokens"]
    assert (
        api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json").status_code
        == 401
    )

    client = make_auth_client(patient_user)
    assert client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json").status_code == 204
    assert (
        api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json").status_code
        == 401
    )


def test_me_and_consents(auth_client: APIClient, patient_user: User) -> None:
    me = auth_client.get(ME_URL)
    assert me.status_code == 200
    assert me.json()["email"] == patient_user.email
    assert me.json()["auth_providers"] == ["PASSWORD"]
    assert auth_client.patch(ME_URL, {"full_name": "X"}, format="json").status_code == 405

    first = auth_client.post(
        CONSENTS_URL,
        {"consents": [{"document_type": "HEALTH_DATA_PROCESSING", "version": CONSENT_VERSION}]},
        format="json",
    )
    assert first.status_code == 201
    second = auth_client.post(
        CONSENTS_URL,
        {"consents": [{"document_type": "HEALTH_DATA_PROCESSING", "version": CONSENT_VERSION}]},
        format="json",
    )
    assert second.status_code == 201
    assert (
        ConsentRecord.objects.filter(
            user=patient_user, document_type="HEALTH_DATA_PROCESSING"
        ).count()
        == 1
    )
    listed = auth_client.get(CONSENTS_ME_URL)
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)


def test_consents_reject_outdated_version(auth_client: APIClient) -> None:
    response = auth_client.post(
        CONSENTS_URL,
        {"consents": [{"document_type": "HEALTH_DATA_PROCESSING", "version": "old"}]},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_change_password(auth_client: APIClient, patient_user: User) -> None:
    wrong = auth_client.post(
        CHANGE_PASSWORD_URL,
        {
            "current_password": "wrong-password",
            "new_password": "N3w-Strong-Password!",
            "confirm_password": "N3w-Strong-Password!",
        },
        format="json",
    )
    assert wrong.status_code == 400
    assert "current_password" in wrong.json()["details"]

    ok = auth_client.post(
        CHANGE_PASSWORD_URL,
        {
            "current_password": DEFAULT_PASSWORD,
            "new_password": "N3w-Strong-Password!",
            "confirm_password": "N3w-Strong-Password!",
        },
        format="json",
    )
    assert ok.status_code == 204
    patient_user.refresh_from_db()
    assert patient_user.check_password("N3w-Strong-Password!")


def test_auth_throttle_returns_envelope_and_retry_after(api_client: APIClient) -> None:
    for _ in range(5):
        response = api_client.post(
            LOGIN_URL, {"email": "nobody@example.com", "password": "wrong-password"}, format="json"
        )
        assert response.status_code == 401
    throttled = api_client.post(
        LOGIN_URL, {"email": "nobody@example.com", "password": "wrong-password"}, format="json"
    )
    assert throttled.status_code == 429
    assert throttled.json()["code"] == "throttled"
    assert throttled["Retry-After"]


def test_axes_lockout_returns_account_locked_envelope(api_client: APIClient) -> None:
    AccessAttempt.objects.all().delete()
    payload = {"email": "locked@example.com", "password": "wrong-password"}

    with override_settings(
        AXES_ENABLED=True,
        AXES_FAILURE_LIMIT=5,
        AXES_COOLOFF_TIME=timedelta(minutes=15),
    ):
        response = None
        for _ in range(5):
            # Clear only the DRF throttle so axes' database-backed lockout is
            # the behaviour under test.
            cache.clear()
            response = api_client.post(LOGIN_URL, payload, format="json")

        assert response is not None
        assert response.status_code == 429
        assert response.json()["code"] == "account_locked"
        assert response["Retry-After"] == "900"
        assert response.json()["request_id"] == response["X-Request-ID"]

        cache.clear()
        locked = api_client.post(LOGIN_URL, payload, format="json")
        assert locked.status_code == 429
        assert locked.json()["code"] == "account_locked"


def test_request_id_propagation_and_error_envelope(api_client: APIClient) -> None:
    response = api_client.get("/healthz", HTTP_X_REQUEST_ID="client-request-123")
    assert response["X-Request-ID"] == "client-request-123"
    error = api_client.get(ME_URL, HTTP_X_REQUEST_ID="envelope-123")
    assert error.status_code == 401
    assert error.json()["code"] == "not_authenticated"
    assert error.json()["request_id"] == "envelope-123"


def test_google_view_success_and_failure(api_client: APIClient, patient_user: User) -> None:
    from apps.accounts.services import GoogleLoginResult

    with patch(
        "apps.accounts.views.GoogleLoginService.login",
        return_value=GoogleLoginResult(user=patient_user, is_new_user=False),
    ):
        success = api_client.post(GOOGLE_URL, {"id_token": "fake"}, format="json")
    assert success.status_code == 200
    assert success.json()["is_new_user"] is False

    from apps.core.exceptions import ConsentRequiredInput

    with patch("apps.accounts.views.GoogleLoginService.login", side_effect=ConsentRequiredInput()):
        failure = api_client.post(GOOGLE_URL, {"id_token": "fake"}, format="json")
    assert failure.status_code == 400
    assert failure.json()["code"] == "consent_required"


def test_user_model_normalises_email() -> None:
    user = get_user_model().objects.create_user(
        email="MixedCase@Example.COM", password="A-very-l0ng-password!", full_name="Mixed Case"
    )
    assert user.email == "mixedcase@example.com"
    assert user.get_full_name() == "Mixed Case"


def test_send_test_email_command() -> None:
    from django.core import mail
    from django.core.management import call_command

    call_command("send_test_email", "contacto@adelgaceya.pe")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["contacto@adelgaceya.pe"]
    assert "Adelgaceya" in mail.outbox[0].subject
