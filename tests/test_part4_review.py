"""Contract Part 4 section 4: evaluation queue, claims, review packet and decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Assessment, Evaluation, LabUpload, Program
from apps.clinical.documents import generate_pdf
from apps.clinical.evaluations import auto_release_stale_claims
from apps.clinical.models import PrescriptionDocument, Specialty
from apps.core.models import AuditLog
from apps.notifications.models import Notification
from apps.treatments.models import Product, Treatment
from tests.factories.part3 import idem, make_physician
from tests.factories.users import PatientFactory
from tests.test_assessments_part2 import complete_steps, give_consents, start_assessment, submit

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _waive_fee() -> None:
    Program.objects.filter(slug="weight-loss").update(fee_is_waived=True)


@pytest.fixture
def physician(db: Any) -> User:
    return make_physician()


@pytest.fixture
def doctor(physician: User, make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(physician)


def submitted(make_auth_client: Callable[[User], APIClient], **steps: Any) -> Evaluation:
    patient = PatientFactory()
    client = make_auth_client(patient)
    give_consents(patient)
    _code, body = start_assessment(client, patient)
    complete_steps(client, body["id"], **steps)
    assert submit(client, body["id"])["evaluation_status"] == "SUBMITTED"
    return Evaluation.objects.get(assessment_id=body["id"])


def claim(client: APIClient, evaluation: Evaluation) -> dict[str, Any]:
    response = client.post(f"/api/v1/clinical/evaluations/{evaluation.pk}/claim/")
    assert response.status_code == 200, response.json()
    return response.json()


def approve_body(**overrides: Any) -> dict[str, Any]:
    product = Product.objects.get(code="semaglutide-0-5")
    today = timezone.localdate()
    body: dict[str, Any] = {
        "attestation": True,
        "acknowledged_flag_option_ids": [],
        "prescription": {
            "start_date": today.isoformat(),
            "valid_until": (today + timedelta(days=180)).isoformat(),
            "items": [
                {
                    "product_id": str(product.pk),
                    "dose": {"value": "0.25", "unit": "mg"},
                    "route": "SUBCUTANEOUS",
                    "frequency": "WEEKLY",
                    "day_of_week": 2,
                    "time_of_day": "MORNING",
                    "duration_weeks": 12,
                    "refills_total": 2,
                    "instructions": "Morning with water",
                }
            ],
        },
        "patient_note": "Start low and go slow.",
        "internal_note": "Healthy candidate.",
    }
    body.update(overrides)
    return body


def decide(
    client: APIClient, evaluation: Evaluation, action: str, body: dict[str, Any], version: Any
) -> Any:
    return client.post(
        f"/api/v1/clinical/evaluations/{evaluation.pk}/{action}/",
        body,
        format="json",
        HTTP_IF_MATCH=f'"{version}"',
        HTTP_IDEMPOTENCY_KEY=idem(),
    )


def test_unverified_physician_is_blocked(make_auth_client: Callable[[User], APIClient]) -> None:
    pending = make_physician(name="New Doc", verification_status="PENDING")
    response = make_auth_client(pending).get("/api/v1/clinical/evaluations/")
    assert response.status_code == 403
    assert response.json()["code"] == "credentials_not_verified"
    assert response.json()["details"] == {"verification_status": "PENDING"}


def test_queue_claim_and_counts(
    doctor: APIClient,
    physician: User,
    make_auth_client: Callable[[User], APIClient],
) -> None:
    evaluation = submitted(make_auth_client, medical_label="Diabetes")
    assert Notification.objects.filter(user=physician, type="NEW_EVALUATION_IN_QUEUE").exists()
    queue = doctor.get("/api/v1/clinical/evaluations/").json()
    assert queue["counts"] == {"all": 1, "queue": 1, "mine": 0}
    item = queue["results"][0]
    assert item["status"] == "SUBMITTED"
    assert item["screening_result"] == "NEEDS_REVIEW"
    assert item["labs_state"] == "MISSING"
    assert item["questionnaire_state"] == "COMPLETE"
    assert item["patient"]["age_years"] >= 18
    assert item["assigned_physician"] is None
    assert doctor.get("/api/v1/clinical/evaluations/?labs=MISSING").json()["count"] == 1
    assert doctor.get("/api/v1/clinical/evaluations/?labs=ATTACHED").json()["count"] == 0

    claimed = claim(doctor, evaluation)
    assert claimed["status"] == "IN_REVIEW"
    assert claimed["assigned_physician"]["id"] == str(physician.pk)
    assert claim(doctor, evaluation)["version"] == claimed["version"]  # resume

    other = make_physician(name="Other Doc")
    second = make_auth_client(other).post(f"/api/v1/clinical/evaluations/{evaluation.pk}/claim/")
    assert second.status_code == 409 and second.json()["code"] == "already_claimed"
    mine = doctor.get("/api/v1/clinical/evaluations/?scope=MINE").json()
    assert mine["counts"] == {"all": 1, "queue": 0, "mine": 1}
    assert doctor.get("/api/v1/clinical/evaluations/?scope=BAD").status_code == 400


def test_queue_visibility_by_specialty(
    make_auth_client: Callable[[User], APIClient],
) -> None:
    endo = Specialty.objects.get(name="Endocrinology")
    program = Program.objects.get(slug="weight-loss")
    program.allowed_specialties.add(endo)
    evaluation = submitted(make_auth_client)
    cardio = make_physician(name="Cardio", specialty_ref=Specialty.objects.get(name="Cardiology"))
    allowed = make_physician(name="Endo", specialty_ref=endo)
    assert make_auth_client(cardio).get("/api/v1/clinical/evaluations/").json()["count"] == 0
    assert make_auth_client(allowed).get("/api/v1/clinical/evaluations/").json()["count"] == 1
    hidden = make_auth_client(cardio).post(f"/api/v1/clinical/evaluations/{evaluation.pk}/claim/")
    assert hidden.status_code == 404


def test_packet(
    doctor: APIClient, physician: User, make_auth_client: Callable[[User], APIClient]
) -> None:
    evaluation = submitted(
        make_auth_client,
        medical_label="Diabetes",
        takes_medications="YES",
        medication_text="Metformin 850mg",
    )
    url = f"/api/v1/clinical/evaluations/{evaluation.pk}/"
    unclaimed = doctor.get(url)
    assert unclaimed.status_code == 403 and unclaimed.json()["code"] == "claim_required"
    claim(doctor, evaluation)
    upload = LabUpload.objects.create(
        assessment=evaluation.assessment,
        uploaded_by=evaluation.assessment.user,
        file_name="Blood test.pdf",
        storage_key="labs/test.pdf",
        content_type="application/pdf",
        size_bytes=2400000,
        status="CLEAN",
    )
    response = doctor.get(url)
    assert response.status_code == 200
    assert response["ETag"] == f'"{response.json()["version"]}"'
    body = response.json()
    assert body["labs_state"] == "ATTACHED"
    assert body["attachments"][0]["id"] == str(upload.pk)
    groups = {block["group"]: block for block in body["questionnaire"]}
    assert list(groups)[:4] == [
        "PRIMARY_CONDITIONS",
        "CONTRAINDICATION_SCREENING",
        "CURRENT_MEDICATIONS",
        "WEIGHT_LOSS_HISTORY",
    ]
    assert groups["PRIMARY_CONDITIONS"]["items"][0]["answer"] == "Diabetes"
    assert {item["answer"] for item in groups["CURRENT_MEDICATIONS"]["items"]} == {
        "Yes",
        "Metformin 850mg",
    }
    assert groups["WEIGHT_LOSS_HISTORY"]["items"] == []
    assert body["screening_flags"][0]["option_label"] == "Diabetes"
    assert body["screening_flags"][0]["effect"] == "NEEDS_REVIEW"
    assert body["biometrics"]["current_weight"]["kg"] == 82.0
    assert body["biometrics"]["bmi_category"] in ("OVERWEIGHT", "OBESE")
    assert body["patient"]["date_of_birth"] == "1990-01-01"
    assert AuditLog.objects.filter(action="evaluation.packet_read").count() == 1


def test_weight_loss_history_question_is_hidden_group(
    make_auth_client: Callable[[User], APIClient], patient_user: User
) -> None:
    client = make_auth_client(patient_user)
    give_consents(patient_user)
    _code, body = start_assessment(client, patient_user)
    questions = client.get(f"/api/v1/assessments/{body['id']}/questionnaire/").json()
    keys = [q["key"] for step in questions["steps"] for q in step["questions"]]
    assert "weight_loss_history" in keys
    serialized = str(questions)
    assert "review_group" not in serialized


def test_release_and_concurrency(
    doctor: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    evaluation = submitted(make_auth_client)
    version = claim(doctor, evaluation)["version"]
    url = f"/api/v1/clinical/evaluations/{evaluation.pk}/release/"
    missing = doctor.post(url, {}, format="json")
    assert missing.status_code == 428 and missing.json()["code"] == "precondition_required"
    stale = doctor.post(url, {}, format="json", HTTP_IF_MATCH='"1"')
    assert stale.status_code == 412 and stale.json()["code"] == "precondition_failed"
    released = doctor.post(
        url, {"reason": "Out of office"}, format="json", HTTP_IF_MATCH=f'W/"{version}"'
    )
    assert released.status_code == 200
    assert released.json()["status"] == "SUBMITTED"
    assert released.json()["assigned_physician"] is None


def test_request_labs_cycle(
    doctor: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    evaluation = submitted(make_auth_client)
    version = claim(doctor, evaluation)["version"]
    panels = doctor.get("/api/v1/clinical/lab-panels/").json()
    assert {panel["code"] for panel in panels} >= {"CMP", "HBA1C"}
    bad = decide(doctor, evaluation, "request-labs", {"panels": ["NOPE"]}, version)
    assert bad.status_code == 400
    response = decide(
        doctor,
        evaluation,
        "request-labs",
        {"panels": ["HBA1C"], "message": "Please get A1c"},
        version,
    )
    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "PENDING_LABS"
    evaluation.refresh_from_db()
    assert evaluation.lab_recommendations_source == "PHYSICIAN"
    assert [panel["code"] for panel in evaluation.lab_recommendations] == ["HBA1C"]
    patient = evaluation.assessment.user
    assert Notification.objects.filter(user=patient, type="LABS_REQUESTED").exists()

    patient_client = make_auth_client(patient)
    recommendations = patient_client.get(
        f"/api/v1/assessments/{evaluation.assessment_id}/lab-recommendations/"
    ).json()
    assert recommendations["recommended_by"] == "PHYSICIAN"
    LabUpload.objects.create(
        assessment=evaluation.assessment,
        uploaded_by=patient,
        file_name="a1c.pdf",
        storage_key="labs/a1c.pdf",
        content_type="application/pdf",
        size_bytes=10,
        status="CLEAN",
    )
    submitted_labs = patient_client.post(
        f"/api/v1/assessments/{evaluation.assessment_id}/labs/submit/", {}, format="json"
    )
    assert submitted_labs.json()["evaluation_status"] == "SUBMITTED"
    mine = doctor.get("/api/v1/clinical/evaluations/?scope=MINE").json()
    assert mine["results"][0]["id"] == str(evaluation.pk)
    assert Notification.objects.filter(type="EVALUATION_LABS_UPLOADED").exists()
    assert claim(doctor, evaluation)["status"] == "IN_REVIEW"


def test_reject(doctor: APIClient, make_auth_client: Callable[[User], APIClient]) -> None:
    evaluation = submitted(make_auth_client)
    version = claim(doctor, evaluation)["version"]
    body = {
        "reason_code": "IN_PERSON_CARE_RECOMMENDED",
        "internal_note": "Requires in-person thyroid workup.",
        "patient_note": "Please see a specialist in person.",
        "attestation": True,
    }
    assert (
        decide(doctor, evaluation, "reject", {**body, "attestation": False}, version).status_code
        == 400
    )
    assert (
        decide(
            doctor, evaluation, "reject", {**body, "internal_note": "short"}, version
        ).status_code
        == 400
    )
    response = decide(doctor, evaluation, "reject", body, version)
    assert response.status_code == 200
    assert response.json()["status"] == "NOT_SUITABLE"
    evaluation.refresh_from_db()
    assert evaluation.rejection_reason_code == "IN_PERSON_CARE_RECOMMENDED"
    patient_view = (
        make_auth_client(evaluation.assessment.user)
        .get(f"/api/v1/assessments/{evaluation.assessment_id}/evaluation/")
        .json()
    )
    assert patient_view["status"] == "NOT_SUITABLE"
    assert patient_view["patient_note"] == "Please see a specialist in person."
    assert "Requires in-person" not in str(patient_view)
    again = decide(doctor, evaluation, "reject", body, evaluation.version)
    assert again.json()["code"] == "invalid_state_transition"


def test_approve_and_prescribe(
    doctor: APIClient, physician: User, make_auth_client: Callable[[User], APIClient]
) -> None:
    evaluation = submitted(make_auth_client, medical_label="Diabetes")
    version = claim(doctor, evaluation)["version"]
    flags = doctor.get(f"/api/v1/clinical/evaluations/{evaluation.pk}/").json()["screening_flags"]
    flag_ids = [flag["option_id"] for flag in flags]

    missing = decide(doctor, evaluation, "approve", approve_body(), version)
    assert missing.status_code == 400 and missing.json()["code"] == "flags_not_acknowledged"
    assert missing.json()["details"]["missing"] == flag_ids

    bad_dose = approve_body(acknowledged_flag_option_ids=flag_ids)
    bad_dose["prescription"]["items"][0]["dose"] = {"value": "2.4", "unit": "mg"}
    response = decide(doctor, evaluation, "approve", bad_dose, version)
    assert response.status_code == 400 and response.json()["code"] == "dose_out_of_range"

    too_many = approve_body(acknowledged_flag_option_ids=flag_ids)
    too_many["prescription"]["items"][0]["refills_total"] = 9
    assert decide(doctor, evaluation, "approve", too_many, version).status_code == 400
    no_day = approve_body(acknowledged_flag_option_ids=flag_ids)
    no_day["prescription"]["items"][0].pop("day_of_week")
    assert decide(doctor, evaluation, "approve", no_day, version).status_code == 400
    late = approve_body(acknowledged_flag_option_ids=flag_ids)
    late["prescription"]["valid_until"] = (timezone.localdate() + timedelta(days=500)).isoformat()
    assert decide(doctor, evaluation, "approve", late, version).status_code == 400

    key = idem()
    good = approve_body(acknowledged_flag_option_ids=flag_ids)
    url = f"/api/v1/clinical/evaluations/{evaluation.pk}/approve/"
    response = doctor.post(
        url, good, format="json", HTTP_IF_MATCH=f'"{version}"', HTTP_IDEMPOTENCY_KEY=key
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["evaluation"]["status"] == "APPROVED"
    item = body["prescription"]["items"][0]
    assert item["dose"] == {"value": 0.25, "unit": "mg", "label": "0.25 mg"}
    assert item["refills_total"] == 2
    replay = doctor.post(
        url, good, format="json", HTTP_IF_MATCH=f'"{version}"', HTTP_IDEMPOTENCY_KEY=key
    )
    assert replay.json() == body

    treatment = Treatment.objects.get(pk=item["treatment_id"])
    assert treatment.status == "ACTIVE"
    assert treatment.prescribing_physician == physician
    assert treatment.instructions == "Morning with water"
    assert treatment.versions.count() == 1
    assert treatment.prescription_expires_at is not None

    prescription = evaluation.prescription
    key_path = generate_pdf(prescription.pk)
    assert key_path is not None
    with default_storage.open(key_path, "rb") as handle:
        assert handle.read(5) == b"%PDF-"

    patient = evaluation.assessment.user
    patient_client = make_auth_client(patient)
    overview = patient_client.get("/api/v1/patients/me/overview/").json()
    assert overview["primary_action"]["type"] == "ORDER_MEDICINE"
    detail = patient_client.get(f"/api/v1/treatments/{treatment.pk}/").json()
    assert detail["prescription_pdf_available"] is True
    assert detail["current_dose"]["label"] == "0.25 mg"
    assert Notification.objects.filter(user=patient, type="EVALUATION_STATUS_CHANGED").exists()


def test_products_endpoint(doctor: APIClient) -> None:
    program = Program.objects.get(slug="weight-loss")
    body = doctor.get(f"/api/v1/clinical/products/?program_id={program.pk}").json()
    product = body["results"][0]
    assert product["active_ingredient"] == "Semaglutide"
    assert product["starting_doses"] == [{"value": 0.25, "unit": "mg", "label": "0.25 mg"}]
    assert product["max_refills"] == 5
    assert doctor.get("/api/v1/clinical/products/?search=zzz").json()["count"] == 0


def test_signed_prescription_document(
    doctor: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    evaluation = submitted(make_auth_client)
    unclaimed = doctor.post(
        "/api/v1/prescription-documents/",
        {"evaluation_id": str(evaluation.pk), "content_type": "application/pdf", "size_bytes": 9},
        format="json",
    )
    assert unclaimed.status_code == 400
    version = claim(doctor, evaluation)["version"]
    created = doctor.post(
        "/api/v1/prescription-documents/",
        {"evaluation_id": str(evaluation.pk), "content_type": "application/pdf", "size_bytes": 9},
        format="json",
    )
    assert created.status_code == 201
    document = PrescriptionDocument.objects.get(pk=created.json()["id"])
    default_storage.save(document.storage_key, ContentFile(b"%PDF-1.4\n"))
    confirmed = doctor.post(f"/api/v1/prescription-documents/{document.pk}/confirm/")
    assert confirmed.status_code == 200
    from apps.clinical.documents import scan

    scan(document.pk)
    document.refresh_from_db()
    assert document.status == "CLEAN"
    response = decide(
        doctor,
        evaluation,
        "approve",
        approve_body(signed_document_id=str(document.pk)),
        version,
    )
    assert response.status_code == 200, response.json()
    evaluation.refresh_from_db()
    assert evaluation.prescription.pdf_key == document.storage_key


def test_admin_can_read_queue_and_packet(
    admin_client: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    evaluation = submitted(make_auth_client)
    assert admin_client.get("/api/v1/clinical/evaluations/").json()["count"] == 1
    assert admin_client.get(f"/api/v1/clinical/evaluations/{evaluation.pk}/").status_code == 200
    assert (
        admin_client.post(f"/api/v1/clinical/evaluations/{evaluation.pk}/claim/").status_code == 403
    )


def test_auto_release(doctor: APIClient, make_auth_client: Callable[[User], APIClient]) -> None:
    evaluation = submitted(make_auth_client)
    claim(doctor, evaluation)
    assert auto_release_stale_claims() == 0
    Evaluation.objects.filter(pk=evaluation.pk).update(
        last_activity_at=timezone.now() - timedelta(hours=5)
    )
    assert auto_release_stale_claims() == 1
    evaluation.refresh_from_db()
    assert evaluation.status == "SUBMITTED" and evaluation.physician is None
    assert Assessment.objects.count() == 1
