"""WhiteNoise + manifest storage + Swagger UI smoke tests with DEBUG=False."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, override_settings

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def _manifest() -> dict[str, str]:
    path = Path(settings.STATIC_ROOT) / "staticfiles.json"
    return json.loads(path.read_text())["paths"]


def test_hashed_admin_static_asset_is_served() -> None:
    assert settings.DEBUG is False
    hashed = _manifest()["admin/css/base.css"]
    assert hashed != "admin/css/base.css"
    response = Client().get(f"/static/{hashed}")
    assert response.status_code == 200
    assert "text/css" in response["Content-Type"]
    assert "immutable" in response.get("Cache-Control", "") or "max-age" in response.get(
        "Cache-Control", ""
    )


def test_swagger_ui_assets_are_served_from_manifest(staff_user: User) -> None:
    manifest = _manifest()
    asset_key = next(
        key for key in manifest if key.endswith("swagger-ui-bundle.js") and "LICENSE" not in key
    )
    asset = Client().get(f"/static/{manifest[asset_key]}")
    assert asset.status_code == 200
    assert "javascript" in asset["Content-Type"]

    client = Client()
    client.force_login(staff_user)
    docs = client.get("/api/docs/")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.content.decode()
    assert "'unsafe-inline'" in docs["Content-Security-Policy"]


def test_docs_are_staff_only_in_non_debug(staff_user: User, patient_user: User) -> None:
    anonymous = Client().get("/api/docs/")
    assert anonymous.status_code in {401, 403}

    patient = Client()
    patient.force_login(patient_user)
    assert patient.get("/api/docs/").status_code in {401, 403}

    staff = Client()
    staff.force_login(staff_user)
    assert staff.get("/api/docs/").status_code == 200
    assert staff.get("/api/redoc/").status_code == 200
    assert staff.get("/api/schema/").status_code == 200


def test_docs_open_without_login_when_public() -> None:
    with override_settings(API_DOCS_PUBLIC=True):
        anonymous = Client()
        assert anonymous.get("/api/docs/").status_code == 200
        assert anonymous.get("/api/redoc/").status_code == 200
        assert anonymous.get("/api/schema/").status_code == 200


def test_docs_return_404_when_disabled(staff_user: User) -> None:
    client = Client()
    client.force_login(staff_user)
    with override_settings(ENABLE_API_DOCS=False):
        assert client.get("/api/docs/").status_code == 404
        assert client.get("/api/schema/").status_code == 404


def test_admin_csp_is_relaxed_only_for_admin(staff_user: User, make_auth_client: object) -> None:
    client = Client()
    client.force_login(staff_user)
    admin = client.get("/admin/")
    assert admin.status_code == 200
    assert "'unsafe-inline'" in admin["Content-Security-Policy"]

    api = make_auth_client(staff_user).get("/api/v1/auth/me/")  # type: ignore[operator]
    assert api.status_code == 200
    assert "'unsafe-inline'" not in api["Content-Security-Policy"]
