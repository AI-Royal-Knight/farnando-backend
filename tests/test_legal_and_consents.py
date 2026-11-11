"""Legal documents and consent endpoints (contract 2.x)."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import ConsentRecord

pytestmark = pytest.mark.django_db

VERSION = "2026-11-01"


def test_legal_document_list_returns_current_versions(client: APIClient) -> None:
    response = client.get("/api/v1/legal-documents/")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 4
    assert {item["document_type"] for item in payload} == {
        "TERMS_OF_SERVICE",
        "PRIVACY_POLICY",
        "HEALTH_DATA_PROCESSING",
        "CLINICIAN_AGREEMENT",
    }
    assert all(item["version"] == VERSION for item in payload)
    assert "public" in response["Cache-Control"]


@pytest.mark.parametrize(
    ("slug", "document_type"),
    [
        ("terms-of-service", "TERMS_OF_SERVICE"),
        ("privacy-policy", "PRIVACY_POLICY"),
        ("health-data-processing", "HEALTH_DATA_PROCESSING"),
    ],
)
def test_legal_document_detail(client: APIClient, slug: str, document_type: str) -> None:
    response = client.get(f"/api/v1/legal-documents/{slug}/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == document_type
    assert isinstance(payload["sections"], list)


def test_unknown_legal_document_slug_is_404(client: APIClient) -> None:
    assert client.get("/api/v1/legal-documents/cookies/").status_code == 404


def test_consent_me_requires_auth(client: APIClient) -> None:
    assert client.get("/api/v1/consents/me/").status_code == 401


def test_post_consent_and_idempotent_current_flag(auth_client: APIClient, patient_user) -> None:
    response = auth_client.post(
        "/api/v1/consents/",
        {"consents": [{"document_type": "HEALTH_DATA_PROCESSING", "version": VERSION}]},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()[0]["is_current"] is True

    listed = auth_client.get("/api/v1/consents/me/")
    assert listed.status_code == 200
    assert listed.json()[0]["document_type"] == "HEALTH_DATA_PROCESSING"


def test_consent_outdated_version_is_validation_error(auth_client: APIClient) -> None:
    response = auth_client.post(
        "/api/v1/consents/",
        {"consents": [{"document_type": "TERMS_OF_SERVICE", "version": "2019-01-01"}]},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_consent_unknown_field_is_rejected(auth_client: APIClient) -> None:
    response = auth_client.post(
        "/api/v1/consents/",
        {
            "consents": [
                {
                    "document_type": "TERMS_OF_SERVICE",
                    "version": VERSION,
                    "accepted_at": "2020-01-01T00:00:00Z",
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_field"


def test_consent_unknown_enum_is_validation_error(auth_client: APIClient) -> None:
    response = auth_client.post(
        "/api/v1/consents/",
        {"consents": [{"document_type": "MARKETING", "version": VERSION}]},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_consent_record_has_expected_fields(auth_client: APIClient, patient_user) -> None:
    ConsentRecord.objects.create(
        user=patient_user,
        document_type=ConsentRecord.DocumentType.TERMS_OF_SERVICE,
        version=VERSION,
    )
    payload = auth_client.get("/api/v1/consents/me/").json()
    assert set(payload[0]) == {"document_type", "version", "accepted_at", "is_current"}
