"""End-to-end API tests for registration, login, tokens and account lifecycle."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

from apps.accounts.models import ConsentRecord, User
from apps.accounts.services import GoogleLoginError, encode_uid
from apps.accounts.tokens import email_verification_token
from tests.factories.users import DEFAULT_PASSWORD, PatientFactory, UnverifiedUserFactory

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/v1/auth/register/"
LOGIN_URL = "/api/v1/auth/login/"
REFRESH_URL = "/api/v1/auth/token/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
VERIFY_URL = "/api/v1/auth/verify-email/"
RESEND_URL = "/api/v1/auth/verify-email/resend/"
RESET_URL = "/api/v1/auth/password-reset/"
RESET_CONFIRM_URL = "/api/v1/auth/password-reset/confirm/"
ME_URL = "/api/v1/auth/me/"
CONSENTS_URL = "/api/v1/auth/consents/"
GOOGLE_URL = "/api/v1/auth/google/"


def registration_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "email": "nueva@example.com",
        "password": "A-very-l0ng-password!",
        "password_confirm": "A-very-l0ng-password!",
        "first_name": "Ana",
        "last_name": "Quispe",
        "accept_terms": True,
        "accept_privacy": True,
    }
    payload.update(overrides)
    return payload


def api_login(client: APIClient, user: User, password: str = DEFAULT_PASSWORD) -> Any:
    return client.post(
        LOGIN_URL,
        {"email": user.email, "password": password},
        format="json",
    )


def query_from_email(body: str, path_fragment: str) -> dict[str, str]:
    match = re.search(rf"https?://\S*{re.escape(path_fragment)}\?\S+", body)
    assert match, body
    return {key: values[0] for key, values in parse_qs(urlparse(match.group(0)).query).items()}


def test_registration_creates_patient_and_records_consents(
    api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(REGISTER_URL, registration_payload(), format="json")

    assert response.status_code == 202
    assert response.json()["detail"].startswith("If the address can be used")
    user = User.objects.get(email="nueva@example.com")
    assert user.role == "PATIENT"
    assert user.is_email_verified is False
    assert user.consents.count() == 2
    assert set(user.consents.values_list("document_type", flat=True)) == {"TERMS", "PRIVACY"}
    assert len(mail.outbox) == 1
    assert "verify-email" in mail.outbox[0].body


def test_registration_does_not_reveal_existing_email(
    api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    existing = PatientFactory(email="known@example.com")

    with django_capture_on_commit_callbacks(execute=True):
        response = api_client.post(
            REGISTER_URL, registration_payload(email="known@example.com"), format="json"
        )

    assert response.status_code == 202
    assert User.objects.filter(email="known@example.com").count() == 1
    assert len(mail.outbox) == 1
    assert "Ya existe una cuenta" in mail.outbox[0].body
    assert existing.is_email_verified is True


def test_registration_rejects_weak_password_and_missing_consent(api_client: APIClient) -> None:
    weak = api_client.post(
        REGISTER_URL,
        registration_payload(password="short", password_confirm="short"),
        format="json",
    )
    assert weak.status_code == 400
    assert "password" in weak.json()["details"]

    no_consent = api_client.post(
        REGISTER_URL, registration_payload(accept_terms=False), format="json"
    )
    assert no_consent.status_code == 400
    assert "consents" in no_consent.json()["details"]


def test_registration_does_not_create_user_when_password_is_weak(api_client: APIClient) -> None:
    api_client.post(
        REGISTER_URL,
        registration_payload(password="short", password_confirm="short"),
        format="json",
    )
    assert not User.objects.filter(email="nueva@example.com").exists()


def test_login_unverified_user_and_me(api_client: APIClient) -> None:
    user = UnverifiedUserFactory(email="unverified@example.com")
    response = api_login(api_client, user)

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "Bearer"
    assert data["user"]["email"] == user.email
    assert data["user"]["is_email_verified"] is False
    assert data["access"] and data["refresh"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {data['access']}")
    me = api_client.get(ME_URL)
    assert me.status_code == 200
    assert me.json()["email"] == user.email


def test_login_errors_are_uniform(api_client: APIClient) -> None:
    PatientFactory(email="exists@example.com")
    wrong_password = api_client.post(
        LOGIN_URL, {"email": "exists@example.com", "password": "nope-nope-nope"}, format="json"
    )
    unknown = api_client.post(
        LOGIN_URL, {"email": "missing@example.com", "password": "nope-nope-nope"}, format="json"
    )

    assert wrong_password.status_code == unknown.status_code == 401
    assert wrong_password.json()["message"] == unknown.json()["message"]
    assert wrong_password.json()["code"] == unknown.json()["code"]


def test_inactive_user_receives_uniform_error(api_client: APIClient) -> None:
    user = PatientFactory(is_active=False)
    response = api_login(api_client, user)
    assert response.status_code == 401
    assert response.json()["message"] == "Invalid email or password."


def test_verify_email_flow(api_client: APIClient, django_capture_on_commit_callbacks: Any) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        api_client.post(REGISTER_URL, registration_payload(), format="json")
    params = query_from_email(mail.outbox[0].body, "/verify-email")

    response = api_client.post(VERIFY_URL, params, format="json")
    assert response.status_code == 200
    user = User.objects.get(email="nueva@example.com")
    assert user.is_email_verified is True
    assert user.email_verified_at is not None

    reused = api_client.post(VERIFY_URL, params, format="json")
    assert reused.status_code == 400
    assert reused.json()["details"]["token"]


def test_verify_email_rejects_invalid_token(api_client: APIClient) -> None:
    user = UnverifiedUserFactory()
    response = api_client.post(
        VERIFY_URL,
        {"uid": encode_uid(user.pk), "token": "not-a-token"},
        format="json",
    )
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.is_email_verified is False


def test_verify_email_token_invalidated_by_password_change() -> None:
    user = UnverifiedUserFactory()
    token = email_verification_token.make_token(user)
    user.set_password("An0ther-Strong-Pass!")
    user.save()
    assert email_verification_token.check_token(user, token) is False


def test_resend_verification_email(
    api_client: APIClient,
    unverified_user: User,
    make_auth_client: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    client = make_auth_client(unverified_user)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(RESEND_URL, {}, format="json")
    assert response.status_code == 202
    assert len(mail.outbox) == 1

    verified = PatientFactory()
    with django_capture_on_commit_callbacks(execute=True):
        already = make_auth_client(verified).post(RESEND_URL, {}, format="json")
    assert already.status_code == 400


def test_password_reset_flow_and_token_revocation(
    api_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    user = PatientFactory(email="reset@example.com")
    login = api_login(api_client, user)
    old_refresh = login.json()["refresh"]

    with django_capture_on_commit_callbacks(execute=True):
        request_response = api_client.post(RESET_URL, {"email": "reset@example.com"}, format="json")
    assert request_response.status_code == 202
    params = query_from_email(mail.outbox[0].body, "/reset-password")

    confirm = api_client.post(
        RESET_CONFIRM_URL,
        {
            **params,
            "new_password": "N3w-Strong-Password!",
            "new_password_confirm": "N3w-Strong-Password!",
        },
        format="json",
    )
    assert confirm.status_code == 204
    user.refresh_from_db()
    assert user.check_password("N3w-Strong-Password!")

    refresh_after = api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json")
    assert refresh_after.status_code == 401


def test_password_reset_does_not_reveal_unknown_email(api_client: APIClient) -> None:
    PatientFactory(email="known-reset@example.com")
    known_response = api_client.post(RESET_URL, {"email": "known-reset@example.com"}, format="json")
    unknown_response = api_client.post(RESET_URL, {"email": "unknown@example.com"}, format="json")
    assert known_response.status_code == unknown_response.status_code == 202
    assert known_response.json() == unknown_response.json()


def test_refresh_rotation_and_blacklist(api_client: APIClient) -> None:
    user = PatientFactory()
    login = api_login(api_client, user).json()
    old_refresh = login["refresh"]

    rotated = api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json")
    assert rotated.status_code == 200
    assert rotated.json()["access"]
    assert rotated.json()["refresh"] != old_refresh

    reused = api_client.post(REFRESH_URL, {"refresh": old_refresh}, format="json")
    assert reused.status_code == 401


def test_logout_blacklists_refresh_token(api_client: APIClient) -> None:
    user = PatientFactory()
    tokens = api_login(api_client, user).json()

    response = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 204
    refreshed = api_client.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
    assert refreshed.status_code == 401

    # Logout is idempotent.
    again = api_client.post(LOGOUT_URL, {"refresh": tokens["refresh"]}, format="json")
    assert again.status_code == 204


def test_me_update_cannot_change_role(auth_client: APIClient, patient_user: User) -> None:
    response = auth_client.patch(
        ME_URL,
        {"first_name": "Nueva", "last_name": "Paciente", "role": "ADMIN"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["first_name"] == "Nueva"
    patient_user.refresh_from_db()
    assert patient_user.role == "PATIENT"


def test_consent_creation_is_idempotent_per_version(
    auth_client: APIClient, patient_user: User
) -> None:
    first = auth_client.post(CONSENTS_URL, {"document_type": "HEALTH_DATA"}, format="json")
    second = auth_client.post(CONSENTS_URL, {"document_type": "HEALTH_DATA"}, format="json")

    assert first.status_code == 201
    assert second.status_code == 201
    assert ConsentRecord.objects.filter(user=patient_user, document_type="HEALTH_DATA").count() == 1
    listed = auth_client.get(CONSENTS_URL)
    assert listed.status_code == 200
    assert len(listed.json()["results"]) == 1


def test_consent_rejects_unknown_document_type(auth_client: APIClient) -> None:
    response = auth_client.post(CONSENTS_URL, {"document_type": "MARKETING"}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_auth_throttle_returns_envelope(api_client: APIClient) -> None:
    for _ in range(5):
        response = api_client.post(
            LOGIN_URL,
            {"email": "nobody@example.com", "password": "wrong-password"},
            format="json",
        )
        assert response.status_code == 401
    throttled = api_client.post(
        LOGIN_URL,
        {"email": "nobody@example.com", "password": "wrong-password"},
        format="json",
    )
    assert throttled.status_code == 429
    payload = throttled.json()
    assert payload["code"] == "throttled"
    assert payload["request_id"]


def test_request_id_propagation_and_sanitisation(api_client: APIClient) -> None:
    response = api_client.get("/healthz", HTTP_X_REQUEST_ID="client-request-123")
    assert response.status_code == 200
    assert response["X-Request-ID"] == "client-request-123"

    invalid = api_client.get("/healthz", HTTP_X_REQUEST_ID="bad id with spaces")
    request_id = invalid["X-Request-ID"]
    assert request_id != "bad id with spaces"
    assert re.fullmatch(r"[a-f0-9]{32}", request_id)


def test_error_envelope_contains_request_id(api_client: APIClient) -> None:
    response = api_client.get(ME_URL, HTTP_X_REQUEST_ID="envelope-123")
    assert response.status_code == 401
    payload = response.json()
    assert set(payload) == {"code", "message", "details", "request_id"}
    assert payload["request_id"] == "envelope-123"


def test_google_login_view_success_and_failure(api_client: APIClient, patient_user: User) -> None:
    with patch("apps.accounts.views.GoogleLoginService.login", return_value=patient_user):
        success = api_client.post(GOOGLE_URL, {"id_token": "fake"}, format="json")
    assert success.status_code == 200
    assert success.json()["user"]["email"] == patient_user.email

    with patch(
        "apps.accounts.views.GoogleLoginService.login",
        side_effect=GoogleLoginError("Invalid Google credential."),
    ):
        failure = api_client.post(GOOGLE_URL, {"id_token": "fake"}, format="json")
    assert failure.status_code == 400
    assert failure.json()["details"]["id_token"] == "Invalid Google credential."


def test_me_requires_authentication(api_client: APIClient) -> None:
    assert api_client.get(ME_URL).status_code == 401


def test_login_maps_axes_lockout_to_429() -> None:
    from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

    from apps.accounts.serializers import LoginSerializer
    from apps.core.exceptions import AccountLocked

    serializer = LoginSerializer(
        data={"email": "locked@example.com", "password": "whatever-password"},
        context={"request": None},
    )
    with (
        patch(
            "apps.accounts.serializers.authenticate",
            side_effect=DjangoPermissionDenied("locked"),
        ),
        pytest.raises(AccountLocked),
    ):
        serializer.is_valid(raise_exception=True)


def test_user_model_normalises_email() -> None:
    user = get_user_model().objects.create_user(
        email="MixedCase@Example.COM", password="A-very-l0ng-password!"
    )
    assert user.email == "mixedcase@example.com"
    assert str(user) == "mixedcase@example.com"
