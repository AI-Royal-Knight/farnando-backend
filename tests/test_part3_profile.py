"""Contract Part 3 section 2: profile, preferences, addresses, ubigeo, care team."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.orders.models import Order
from apps.patients.models import Address, PatientProfile, Ubigeo
from tests.factories.part3 import (
    make_address,
    make_evaluation,
    make_nurse,
    make_physician,
    make_treatment,
    package,
)
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db


ADDRESS = {
    "label": "Home",
    "recipient_name": "Ana Quispe",
    "phone": "+51987654321",
    "line1": "Av. Larco 1234",
    "line2": "Dpto 502",
    "reference": "Frente al parque",
    "ubigeo": "150122",
}


def test_profile_defaults(auth_client: APIClient, patient_user: User) -> None:
    response = auth_client.get("/api/v1/patients/me/")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == patient_user.email
    assert body["phone"] is None
    assert body["date_of_birth"] is None
    assert body["sex"] is None
    assert body["default_address_id"] is None
    assert body["preferences"] == {
        "weight_unit": "KG",
        "height_unit": "CM",
        "language": "es",
        "timezone": "America/Lima",
        "notifications": {
            "email_messages": True,
            "email_orders": True,
            "email_reminders": True,
            "email_marketing": False,
        },
    }


def test_profile_reads_clinical_fields_from_assessment(
    auth_client: APIClient, patient_user: User
) -> None:
    make_evaluation(patient_user, "SUBMITTED", with_measurement=True)
    body = auth_client.get("/api/v1/patients/me/").json()
    assert body["date_of_birth"] == "1990-01-01"
    assert body["sex"] == "FEMALE"


def test_profile_patch(auth_client: APIClient, patient_user: User) -> None:
    response = auth_client.patch(
        "/api/v1/patients/me/",
        {
            "phone": "+51911222333",
            "user": {"full_name": "Ana María Quispe"},
            "preferences": {
                "weight_unit": "LB",
                "language": "en",
                "timezone": "America/Bogota",
                "notifications": {"email_marketing": True, "email_orders": False},
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["phone"] == "+51911222333"
    assert body["user"]["full_name"] == "Ana María Quispe"
    assert body["preferences"]["weight_unit"] == "LB"
    assert body["preferences"]["height_unit"] == "CM"
    assert body["preferences"]["timezone"] == "America/Bogota"
    assert body["preferences"]["notifications"]["email_marketing"] is True
    assert body["preferences"]["notifications"]["email_orders"] is False
    profile = PatientProfile.objects.get(user=patient_user)
    assert profile.marketing_opt_in_at is not None

    cleared = auth_client.patch("/api/v1/patients/me/", {"phone": None}, format="json")
    assert cleared.json()["phone"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"date_of_birth": "1990-01-01"},
        {"sex": "MALE"},
        {"email": "x@example.com"},
        {"user": {"email": "x@example.com"}},
    ],
)
def test_profile_patch_read_only(auth_client: APIClient, payload: dict[str, Any]) -> None:
    response = auth_client.patch("/api/v1/patients/me/", payload, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "read_only_field"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"phone": "987654321"}, "validation_error"),
        ({"user": {"full_name": "A"}}, "validation_error"),
        ({"preferences": {"timezone": "Mars/Base"}}, "validation_error"),
        ({"preferences": {"language": "fr"}}, "validation_error"),
        ({"preferences": {"notifications": {"email_marketing": "yes"}}}, "validation_error"),
        ({"preferences": {"theme": "dark"}}, "unknown_field"),
        ({"nickname": "x"}, "unknown_field"),
    ],
)
def test_profile_patch_validation(
    auth_client: APIClient, payload: dict[str, Any], code: str
) -> None:
    response = auth_client.patch("/api/v1/patients/me/", payload, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == code


def test_profile_is_patient_only(staff_client: APIClient) -> None:
    assert staff_client.get("/api/v1/patients/me/").status_code == 403


def test_address_crud_and_default(auth_client: APIClient, patient_user: User) -> None:
    first = auth_client.post("/api/v1/addresses/", ADDRESS, format="json")
    assert first.status_code == 201, first.json()
    body = first.json()
    assert body["is_default"] is True
    assert body["department"] == "Lima"
    assert body["province"] == "Lima"
    assert body["district"] == "Miraflores"
    assert body["country"] == "PE"
    assert body["is_deliverable"] is True

    second = auth_client.post(
        "/api/v1/addresses/", {**ADDRESS, "label": "Work", "ubigeo": "040101"}, format="json"
    ).json()
    assert second["is_default"] is False
    assert second["is_deliverable"] is False
    assert second["department"] == "Arequipa"

    assert auth_client.post(f"/api/v1/addresses/{second['id']}/default/").status_code == 204
    listing = auth_client.get("/api/v1/addresses/").json()
    assert [item["id"] for item in listing if item["is_default"]] == [second["id"]]
    profile = auth_client.get("/api/v1/patients/me/").json()
    assert profile["default_address_id"] == second["id"]

    patched = auth_client.patch(
        f"/api/v1/addresses/{body['id']}/", {"line1": "Calle Nueva 1"}, format="json"
    )
    assert patched.status_code == 200
    assert patched.json()["line1"] == "Calle Nueva 1"

    assert auth_client.delete(f"/api/v1/addresses/{second['id']}/").status_code == 204
    remaining = auth_client.get("/api/v1/addresses/").json()
    assert len(remaining) == 1 and remaining[0]["is_default"] is True
    assert auth_client.delete(f"/api/v1/addresses/{body['id']}/").status_code == 204
    assert auth_client.get("/api/v1/patients/me/").json()["default_address_id"] is None


def test_address_validation(auth_client: APIClient) -> None:
    bad_ubigeo = auth_client.post("/api/v1/addresses/", {**ADDRESS, "ubigeo": "999999"})
    assert bad_ubigeo.status_code == 400
    province = auth_client.post("/api/v1/addresses/", {**ADDRESS, "ubigeo": "1501"})
    assert province.status_code == 400
    phone = auth_client.post("/api/v1/addresses/", {**ADDRESS, "phone": "123"})
    assert phone.status_code == 400
    unknown = auth_client.post("/api/v1/addresses/", {**ADDRESS, "country": "PE"})
    assert unknown.json()["code"] == "unknown_field"


def test_address_limit(auth_client: APIClient, patient_user: User) -> None:
    for _ in range(10):
        make_address(patient_user)
    response = auth_client.post("/api/v1/addresses/", ADDRESS, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_address_scoping(
    auth_client: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    other = PatientFactory()
    address = make_address(other)
    assert auth_client.get(f"/api/v1/addresses/{address.pk}/").status_code == 404
    assert auth_client.delete(f"/api/v1/addresses/{address.pk}/").status_code == 404


def test_address_in_use(auth_client: APIClient, patient_user: User) -> None:
    physician = make_physician()
    _, evaluation = make_evaluation(patient_user, physician=physician)
    treatment = make_treatment(patient_user, physician, evaluation=evaluation)
    address = make_address(patient_user)
    Order.objects.create(
        user=patient_user,
        treatment=treatment,
        package=package(),
        number="ADL-ORD-9999",
        status="IN_TRANSIT",
        placed_at="2026-11-01T00:00:00Z",
        subtotal="1",
        shipping="0",
        tax="0",
        total="1",
        shipping_address=address,
    )
    response = auth_client.delete(f"/api/v1/addresses/{address.pk}/")
    assert response.status_code == 409
    assert response.json()["code"] == "address_in_use"


def test_ubigeo(auth_client: APIClient) -> None:
    departments = auth_client.get("/api/v1/geo/ubigeo/?level=DEPARTMENT")
    assert departments.status_code == 200
    assert departments["Cache-Control"] == "public, max-age=86400"
    assert len(departments.json()) == 25
    assert departments.json()[0] == {"code": "01", "name": "Amazonas", "is_deliverable": None}
    provinces = auth_client.get("/api/v1/geo/ubigeo/?level=PROVINCE&parent=15").json()
    assert provinces == [{"code": "1501", "name": "Lima", "is_deliverable": None}]
    districts = auth_client.get("/api/v1/geo/ubigeo/?level=DISTRICT&parent=1501").json()
    assert len(districts) == 43
    assert all(item["is_deliverable"] is True for item in districts)
    assert auth_client.get("/api/v1/geo/ubigeo/?level=DISTRICT").status_code == 400
    assert auth_client.get("/api/v1/geo/ubigeo/?level=CITY").status_code == 400
    assert auth_client.get("/api/v1/geo/ubigeo/?level=DEPARTMENT&parent=15").status_code == 400
    assert APIClient().get("/api/v1/geo/ubigeo/?level=DEPARTMENT").status_code == 401


def test_ubigeo_open_to_every_role(staff_client: APIClient) -> None:
    assert staff_client.get("/api/v1/geo/ubigeo/?level=DEPARTMENT").status_code == 200


def test_load_ubigeo_command(tmp_path: Any) -> None:
    csv_path = tmp_path / "ubigeo.csv"
    csv_path.write_text(
        "code,name,is_deliverable\n08,Cusco,\n0801,Cusco,\n080101,Cusco,true\n",
        encoding="utf-8",
    )
    call_command("load_ubigeo", str(csv_path), verbosity=0)
    district = Ubigeo.objects.get(code="080101")
    assert district.level == "DISTRICT" and district.is_deliverable is True
    assert district.parent_id == "0801"
    bad = tmp_path / "bad.csv"
    bad.write_text("code,name\n990101,Nowhere\n", encoding="utf-8")
    with pytest.raises(CommandError):
        call_command("load_ubigeo", str(bad), verbosity=0)
    with pytest.raises(CommandError):
        call_command("load_ubigeo", str(tmp_path / "missing.csv"), verbosity=0)


def test_care_team(auth_client: APIClient, patient_user: User) -> None:
    assert auth_client.get("/api/v1/patients/me/care-team/").json() == []
    physician = make_physician()
    nurse = make_nurse()
    make_evaluation(patient_user, "IN_REVIEW", physician=physician)
    profile = PatientProfile.objects.get_or_create(user=patient_user)[0]
    profile.assigned_nurse = nurse
    profile.save()
    team = auth_client.get("/api/v1/patients/me/care-team/").json()
    assert [member["role"] for member in team] == ["PHYSICIAN", "NURSE"]
    assert team[0]["name"] == "Dr. Marcus Vance, MD"
    assert team[0]["headline"] == "Board-Certified Endocrinology & Metabolic Care"
    assert team[1]["name"] == "Nurse Sarah"
    assert Address.objects.count() == 0
