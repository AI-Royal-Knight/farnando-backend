"""Liveness, readiness, request IDs and error envelopes."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_liveness_is_dependency_free(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response["X-Request-ID"]


def test_readiness_ok(client: Client, monkeypatch: Any) -> None:
    for name in ("_check_database", "_check_cache", "_check_broker", "_check_storage"):
        monkeypatch.setattr(f"apps.core.views.{name}", lambda: True)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["checks"] == {
        "database": True,
        "cache": True,
        "broker": True,
        "storage": True,
    }


def test_readiness_reports_dependency_failure(client: Client, monkeypatch: Any) -> None:
    monkeypatch.setattr("apps.core.views._check_database", lambda: True)
    monkeypatch.setattr("apps.core.views._check_cache", lambda: False)
    monkeypatch.setattr("apps.core.views._check_broker", lambda: True)
    monkeypatch.setattr("apps.core.views._check_storage", lambda: True)

    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"]["cache"] is False


def test_readiness_never_leaks_exception_details(client: Client, monkeypatch: Any) -> None:
    def explode() -> bool:
        raise RuntimeError("postgres://user:secret@db.internal/adelgaceya")

    monkeypatch.setattr("apps.core.views._check_database", explode)
    monkeypatch.setattr("apps.core.views._check_cache", lambda: True)
    monkeypatch.setattr("apps.core.views._check_broker", lambda: True)
    monkeypatch.setattr("apps.core.views._check_storage", lambda: True)

    response = client.get("/readyz")
    body = response.content.decode()
    assert response.status_code == 503
    assert "secret" not in body
    assert "db.internal" not in body


def test_404_uses_error_envelope(client: Client) -> None:
    response = client.get("/api/v1/does-not-exist/")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "not_found"
    assert payload["request_id"] == response["X-Request-ID"]


def test_api_method_not_allowed_uses_error_envelope(client: Client) -> None:
    response = client.get("/api/v1/auth/login/")
    assert response.status_code == 405
    payload = response.json()
    assert payload["code"] == "method_not_allowed"
    assert payload["request_id"] == response["X-Request-ID"]
