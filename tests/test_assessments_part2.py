"""Contract Part 2: programs, assessments, fees, labs and patient evaluation."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import ConsentRecord
from apps.assessments.enums import EvaluationStatus, FeeStatus, LabUploadStatus, PaymentStatus
from apps.assessments.models import (
    Assessment,
    Evaluation,
    EvaluationFee,
    LabUpload,
    Payment,
    Prescription,
    Program,
)
from apps.assessments.payments import PaymentService
from apps.assessments.tasks import scan_lab_upload
from apps.core.models import AuditLog
from tests.factories.users import PatientFactory, PhysicianFactory

pytestmark = pytest.mark.django_db

VERSION = "2026-11-01"
CONSENT_TYPES = ("TERMS_OF_SERVICE", "PRIVACY_POLICY", "HEALTH_DATA_PROCESSING")


def give_consents(user: Any) -> None:
    for document_type in CONSENT_TYPES:
        ConsentRecord.objects.get_or_create(user=user, document_type=document_type, version=VERSION)


def program() -> Program:
    return Program.objects.get(slug="weight-loss")


def start_assessment(client: APIClient, user: Any) -> tuple[int, dict[str, Any]]:
    give_consents(user)
    response = client.post(
        "/api/v1/assessments/",
        {"program_id": str(program().id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    return response.status_code, response.json()


def questionnaire(client: APIClient, assessment_id: str) -> dict[str, dict[str, Any]]:
    response = client.get(f"/api/v1/assessments/{assessment_id}/questionnaire/")
    assert response.status_code == 200, response.json()
    questions: dict[str, dict[str, Any]] = {}
    for step in response.json()["steps"]:
        for question in step["questions"]:
            questions[question["key"]] = question
    return questions


def option(question: dict[str, Any], label: str) -> str:
    return next(item["id"] for item in question["options"] if item["label"] == label)


def complete_steps(
    client: APIClient,
    assessment_id: str,
    *,
    safety_label: str = "None of the below",
    medical_label: str = "None of the above",
    takes_medications: str = "NO",
    medication_text: str = "Metformin 500mg daily",
    current_weight: str = "82",
    target_weight: str = "70",
    height: str = "175",
    unit: str = "KG",
    height_unit: str = "CM",
    dob: str = "1990-01-01",
) -> dict[str, Any]:
    questions = questionnaire(client, assessment_id)
    medical = questions["medical_conditions"]
    safety = questions["safety_screening"]
    medication = questions["takes_medications"]

    medical_response = client.put(
        f"/api/v1/assessments/{assessment_id}/steps/medical-conditions/",
        {
            "answers": [
                {
                    "question_id": medical["id"],
                    "option_ids": [option(medical, medical_label)],
                }
            ]
        },
        format="json",
    )
    assert medical_response.status_code == 200, medical_response.json()

    health_answers: list[dict[str, Any]] = [
        {
            "question_id": safety["id"],
            "option_ids": [option(safety, safety_label)],
        },
        {"question_id": medication["id"], "answer": takes_medications},
    ]
    if takes_medications == "YES":
        health_answers.append(
            {"question_id": questions["medication_list"]["id"], "text": medication_text}
        )
    health_response = client.put(
        f"/api/v1/assessments/{assessment_id}/steps/health-conditions/",
        {"answers": health_answers},
        format="json",
    )
    assert health_response.status_code == 200, health_response.json()

    measurements = client.put(
        f"/api/v1/assessments/{assessment_id}/measurements/",
        {
            "current_weight": {"value": current_weight, "unit": unit},
            "target_weight": {"value": target_weight, "unit": unit},
            "height": {"value": height, "unit": height_unit},
            "sex": "MALE",
            "date_of_birth": dob,
        },
        format="json",
    )
    assert measurements.status_code == 200, measurements.json()
    return measurements.json()


def submit(client: APIClient, assessment_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/assessments/{assessment_id}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 200, response.json()
    return response.json()


def submitted_assessment(client: APIClient, user: Any) -> tuple[Assessment, dict[str, Any]]:
    status_code, created = start_assessment(client, user)
    assert status_code == 201, created
    complete_steps(client, created["id"])
    result = submit(client, created["id"])
    assessment = Assessment.objects.get(pk=created["id"])
    return assessment, result


# --------------------------------------------------------------------------- #
# Programs and assessment lifecycle
# --------------------------------------------------------------------------- #


def test_programs_require_authentication(client: APIClient) -> None:
    assert client.get("/api/v1/programs/").status_code == 401


def test_program_list_matches_contract(auth_client: APIClient) -> None:
    response = auth_client.get("/api/v1/programs/")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["slug"] == "weight-loss"
    assert item["name"] == "Weight Loss"
    assert item["is_available"] is True
    assert item["position"] == 1
    assert set(item) == {
        "id",
        "slug",
        "name",
        "description",
        "icon",
        "is_available",
        "position",
    }


def test_create_requires_idempotency_key(auth_client: APIClient, patient_user) -> None:
    give_consents(patient_user)
    response = auth_client.post(
        "/api/v1/assessments/", {"program_id": str(program().id)}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "idempotency_key_required"


def test_create_requires_health_data_consent(auth_client: APIClient) -> None:
    response = auth_client.post(
        "/api/v1/assessments/",
        {"program_id": str(program().id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "consent_required"


def test_create_then_resume_returns_same_draft(auth_client: APIClient, patient_user) -> None:
    status_code, created = start_assessment(auth_client, patient_user)
    assert status_code == 201
    resumed = auth_client.post(
        "/api/v1/assessments/",
        {"program_id": str(program().id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert resumed.status_code == 200
    assert resumed.json()["id"] == created["id"]
    assert resumed.json()["status"] == "DRAFT"


def test_create_unknown_program_is_404(auth_client: APIClient, patient_user) -> None:
    give_consents(patient_user)
    response = auth_client.post(
        "/api/v1/assessments/",
        {"program_id": str(uuid.uuid4())},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 404


def test_unknown_field_is_rejected(auth_client: APIClient) -> None:
    response = auth_client.post(
        "/api/v1/assessments/",
        {"program_id": str(program().id), "unexpected": True},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_field"


def test_questionnaire_hides_screening_effect(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    response = auth_client.get(f"/api/v1/assessments/{created['id']}/questionnaire/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["program"]["name"] == "Weight Loss"
    assert [step["key"] for step in payload["steps"]] == [
        "measurements",
        "medical-conditions",
        "health-conditions",
    ]
    assert "screening_effect" not in response.text
    questions = {
        question["key"]: question for step in payload["steps"] for question in step["questions"]
    }
    assert questions["medication_list"]["show_if"] == {
        "question_id": questions["takes_medications"]["id"],
        "equals": ["YES"],
    }
    assert questions["safety_screening"]["options"][0]["is_exclusive"] is True


def test_other_patient_assessment_is_404(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    other = PatientFactory()
    give_consents(other)
    from rest_framework.test import APIClient as OtherClient
    from rest_framework_simplejwt.tokens import RefreshToken

    other_client = OtherClient()
    other_client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(other).access_token}"
    )
    assert other_client.get(f"/api/v1/assessments/{created['id']}/").status_code == 404


# --------------------------------------------------------------------------- #
# Measurements, answers and summary
# --------------------------------------------------------------------------- #


def test_measurements_are_canonical_and_computed(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    measurements = complete_steps(
        auth_client,
        created["id"],
        current_weight="180.8",
        target_weight="154.3",
        height="68.9",
        unit="LB",
        height_unit="IN",
    )
    assert measurements["current_weight"]["kg"] == pytest.approx(82.0, abs=0.02)
    assert measurements["current_weight"]["display"]["unit"] == "LB"
    assert measurements["height"]["cm"] == pytest.approx(175.0, abs=0.2)
    assert measurements["bmi"] == pytest.approx(26.8, abs=0.1)
    assert measurements["bmi_category"] == "OVERWEIGHT"
    assert measurements["weight_goal_kg"] == pytest.approx(12.0, abs=0.05)
    assert measurements["age_years"] >= 18


def test_measurements_reject_bmi_fields(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    response = auth_client.put(
        f"/api/v1/assessments/{created['id']}/measurements/",
        {
            "current_weight": {"value": "82", "unit": "KG"},
            "target_weight": {"value": "70", "unit": "KG"},
            "height": {"value": "175", "unit": "CM"},
            "sex": "MALE",
            "date_of_birth": "1990-01-01",
            "bmi": 26.8,
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "unknown_field"


def test_target_weight_must_be_lower(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    response = auth_client.put(
        f"/api/v1/assessments/{created['id']}/measurements/",
        {
            "current_weight": {"value": "82", "unit": "KG"},
            "target_weight": {"value": "90", "unit": "KG"},
            "height": {"value": "175", "unit": "CM"},
            "sex": "MALE",
            "date_of_birth": "1990-01-01",
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "target_weight_invalid"


def test_under_age_is_rejected(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    response = auth_client.put(
        f"/api/v1/assessments/{created['id']}/measurements/",
        {
            "current_weight": {"value": "82", "unit": "KG"},
            "target_weight": {"value": "70", "unit": "KG"},
            "height": {"value": "175", "unit": "CM"},
            "sex": "MALE",
            "date_of_birth": date.today().replace(year=date.today().year - 16).isoformat(),
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "under_age"


def test_exclusive_option_cannot_be_combined(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    questions = questionnaire(auth_client, created["id"])
    safety = questions["safety_screening"]
    response = auth_client.put(
        f"/api/v1/assessments/{created['id']}/steps/health-conditions/",
        {
            "answers": [
                {
                    "question_id": safety["id"],
                    "option_ids": [
                        option(safety, "None of the below"),
                        option(safety, "Binge drinking"),
                    ],
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_hidden_answers_are_discarded(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    questions = questionnaire(auth_client, created["id"])
    response = auth_client.put(
        f"/api/v1/assessments/{created['id']}/steps/health-conditions/",
        {
            "answers": [
                {
                    "question_id": questions["safety_screening"]["id"],
                    "option_ids": [option(questions["safety_screening"], "None of the below")],
                },
                {"question_id": questions["takes_medications"]["id"], "answer": "NO"},
                {
                    "question_id": questions["medication_list"]["id"],
                    "text": "Should be discarded",
                },
            ]
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["answers"] == [
        {
            "question_id": questions["safety_screening"]["id"],
            "option_ids": [option(questions["safety_screening"], "None of the below")],
        },
        {"question_id": questions["takes_medications"]["id"], "answer": "NO"},
    ]
    assert response.json()["is_complete"] is True


def test_summary_requires_complete_steps(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    response = auth_client.get(f"/api/v1/assessments/{created['id']}/summary/")
    assert response.status_code == 409
    assert response.json()["code"] == "assessment_incomplete"
    assert response.json()["details"]["missing_steps"] == [
        "measurements",
        "medical-conditions",
        "health-conditions",
    ]


def test_summary_and_projection(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    complete_steps(auth_client, created["id"])
    response = auth_client.get(f"/api/v1/assessments/{created['id']}/summary/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["screening_result"] == "ELIGIBLE"
    assert payload["bmi"] == pytest.approx(26.8, abs=0.1)
    assert payload["weight_goal_kg"] == pytest.approx(12.0, abs=0.05)
    assert payload["estimated_timeline_weeks"] == 12
    assert payload["projection"]["months"] == 3
    assert payload["projection"]["points"][-1]["month"] == 3
    assert payload["projection"]["disclaimer"]
    assert len(payload["bmi_scale"]) == 4


# --------------------------------------------------------------------------- #
# Screening, submission, review and cancellation
# --------------------------------------------------------------------------- #


def test_submit_creates_awaiting_payment_evaluation(auth_client: APIClient, patient_user) -> None:
    assessment, result = submitted_assessment(auth_client, patient_user)
    assert result == {
        "assessment_id": str(assessment.id),
        "evaluation_status": "AWAITING_PAYMENT",
        "screening_result": "ELIGIBLE",
        "next": "PAY_FEE",
    }
    assert assessment.status == "SUBMITTED"
    assert assessment.submitted_at is not None
    fee = EvaluationFee.objects.get(evaluation__assessment=assessment)
    assert fee.status == FeeStatus.PENDING
    assert fee.amount == Decimal("25.00")
    assert fee.currency == "USD"


def test_submit_is_idempotent(auth_client: APIClient, patient_user) -> None:
    _status_code, created = start_assessment(auth_client, patient_user)
    complete_steps(auth_client, created["id"])
    key = str(uuid.uuid4())
    first = auth_client.post(
        f"/api/v1/assessments/{created['id']}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    second = auth_client.post(
        f"/api/v1/assessments/{created['id']}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert Evaluation.objects.count() == 1


def test_submit_requires_verified_email(auth_client: APIClient, patient_user) -> None:
    _status_code, created = start_assessment(auth_client, patient_user)
    complete_steps(auth_client, created["id"])
    patient_user.is_email_verified = False
    patient_user.save(update_fields=["is_email_verified"])
    response = auth_client.post(
        f"/api/v1/assessments/{created['id']}/submit/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 403
    assert response.json()["code"] == "email_not_verified"


def test_unverified_submits_do_not_spend_the_rate_limit(
    auth_client: APIClient, patient_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retries while the email is unverified must not lock the patient out once verified."""
    rates = {**ScopedRateThrottle.THROTTLE_RATES, "assessment_submit": "2/hour"}
    monkeypatch.setattr(ScopedRateThrottle, "THROTTLE_RATES", rates)
    _status_code, created = start_assessment(auth_client, patient_user)
    complete_steps(auth_client, created["id"])
    patient_user.is_email_verified = False
    patient_user.save(update_fields=["is_email_verified"])
    url = f"/api/v1/assessments/{created['id']}/submit/"
    for _attempt in range(4):
        response = auth_client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        assert response.json()["code"] == "email_not_verified"

    patient_user.is_email_verified = True
    patient_user.save(update_fields=["is_email_verified"])
    response = auth_client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert response.status_code == 200, response.json()


def test_assessment_is_locked_after_submit(auth_client: APIClient, patient_user) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    response = auth_client.put(
        f"/api/v1/assessments/{assessment.id}/measurements/",
        {
            "current_weight": {"value": "80", "unit": "KG"},
            "target_weight": {"value": "70", "unit": "KG"},
            "height": {"value": "175", "unit": "CM"},
            "sex": "MALE",
            "date_of_birth": "1990-01-01",
        },
        format="json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "assessment_locked"


def test_screening_not_eligible_creates_screened_out(auth_client: APIClient, patient_user) -> None:
    status_code, created = start_assessment(auth_client, patient_user)
    complete_steps(auth_client, created["id"], safety_label="Personal history of thyroid cancer")
    result = submit(auth_client, created["id"])
    assert result["evaluation_status"] == "SCREENED_OUT"
    assert result["screening_result"] == "NOT_ELIGIBLE"
    assert result["next"] == "SCREENED_OUT_MESSAGE"
    assert status_code == 201


def test_request_review_only_once(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    complete_steps(auth_client, created["id"], safety_label="Personal history of thyroid cancer")
    submit(auth_client, created["id"])
    first = auth_client.post(
        f"/api/v1/assessments/{created['id']}/request-review/",
        {"note": "Please review my case."},
        format="json",
    )
    assert first.status_code == 200
    assert first.json() == {
        "evaluation_status": "SCREENED_OUT",
        "review_requested": True,
    }
    second = auth_client.post(
        f"/api/v1/assessments/{created['id']}/request-review/",
        {},
        format="json",
    )
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"


def test_cancel_awaiting_payment(auth_client: APIClient, patient_user) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    response = auth_client.post(
        f"/api/v1/assessments/{assessment.id}/cancel/",
        {"reason": "Changed my mind."},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["evaluation_status"] == "CANCELLED"
    assessment.refresh_from_db()
    assert assessment.status == "CANCELLED"
    assert assessment.cancellation_reason == "Changed my mind."


def test_cancel_in_review_is_rejected(auth_client: APIClient, patient_user) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    evaluation = Evaluation.objects.get(assessment=assessment)
    evaluation.status = EvaluationStatus.IN_REVIEW
    evaluation.save(update_fields=["status"])
    response = auth_client.post(f"/api/v1/assessments/{assessment.id}/cancel/", {}, format="json")
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"


# --------------------------------------------------------------------------- #
# Fee and payment
# --------------------------------------------------------------------------- #


def test_fee_and_payment_flow(auth_client: APIClient, patient_user) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    fee_response = auth_client.get(f"/api/v1/assessments/{assessment.id}/fee/")
    assert fee_response.status_code == 200
    assert fee_response.json()["amount"] == {"amount": "25.00", "currency": "USD"}
    assert fee_response.json()["payment_methods"] == [{"code": "CULQI", "label": "Culqi"}]
    assert fee_response.json()["payment"] is None

    pay = auth_client.post(
        f"/api/v1/assessments/{assessment.id}/fee/pay/",
        {"payment_method": "CULQI", "provider_token": "tok_test"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert pay.status_code == 200
    assert pay.json()["status"] == "PENDING"
    assert pay.json()["amount"] == {"amount": "25.00", "currency": "USD"}

    payment = Payment.objects.get(evaluation__assessment=assessment)
    PaymentService.mark_succeeded(payment_id=payment.id)
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCEEDED
    evaluation = Evaluation.objects.get(assessment=assessment)
    assert evaluation.status == EvaluationStatus.SUBMITTED
    assert evaluation.fee.status == FeeStatus.PAID


def test_payment_decline_returns_402(auth_client: APIClient, patient_user) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    response = auth_client.post(
        f"/api/v1/assessments/{assessment.id}/fee/pay/",
        {"payment_method": "CULQI", "provider_token": "fail_card"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 402
    assert response.json()["code"] == "payment_failed"


def test_payment_requires_awaiting_payment(auth_client: APIClient, patient_user) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    evaluation = Evaluation.objects.get(assessment=assessment)
    evaluation.status = EvaluationStatus.SUBMITTED
    evaluation.save(update_fields=["status"])
    response = auth_client.post(
        f"/api/v1/assessments/{assessment.id}/fee/pay/",
        {"payment_method": "CULQI", "provider_token": "tok_test"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state_transition"


def test_staff_can_read_fee_but_not_pay(
    auth_client: APIClient,
    patient_user,
    make_auth_client,
    staff_user,
) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    staff_client = make_auth_client(staff_user)
    assert staff_client.get(f"/api/v1/assessments/{assessment.id}/fee/").status_code == 200
    pay = staff_client.post(
        f"/api/v1/assessments/{assessment.id}/fee/pay/",
        {"payment_method": "CULQI", "provider_token": "tok"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert pay.status_code == 403


# --------------------------------------------------------------------------- #
# Labs
# --------------------------------------------------------------------------- #


def _paid_assessment(auth_client: APIClient, patient_user: Any) -> Assessment:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    payment = Payment.objects.create(
        evaluation=assessment.evaluation,
        fee=assessment.evaluation.fee,
        status=PaymentStatus.PENDING,
        amount=Decimal("25.00"),
        currency="USD",
        provider="CULQI",
    )
    PaymentService.mark_succeeded(payment_id=payment.id)
    assessment.refresh_from_db()
    return assessment


def _start_upload(
    client: APIClient, assessment_id: str, content: bytes = b"%PDF-1.7 labs"
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/lab-uploads/",
        {
            "assessment_id": assessment_id,
            "file_name": "BloodTest.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(content),
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    return response.json()


def _put_object(storage_key: str, content: bytes) -> None:
    if default_storage.exists(storage_key):
        default_storage.delete(storage_key)
    default_storage.save(storage_key, ContentFile(content))


def test_lab_recommendations(auth_client: APIClient, patient_user) -> None:
    assessment = _paid_assessment(auth_client, patient_user)
    response = auth_client.get(f"/api/v1/assessments/{assessment.id}/lab-recommendations/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["group_label"] == "Metabolic Care"
    assert payload["recommended_by"] == "SYSTEM"
    assert [panel["code"] for panel in payload["panels"]] == [
        "CMP",
        "HBA1C",
        "INSULIN",
        "LIPID",
    ]


def test_lab_upload_pre_signed_flow(auth_client: APIClient, patient_user) -> None:
    assessment = _paid_assessment(auth_client, patient_user)
    content = b"%PDF-1.7 lab report"
    created = _start_upload(auth_client, str(assessment.id), content)
    assert created["status"] == "PENDING_UPLOAD"
    assert created["upload"]["method"] == "PUT"
    assert created["upload"]["headers"]["Content-Type"] == "application/pdf"

    upload = LabUpload.objects.get(pk=created["id"])
    _put_object(upload.storage_key, content)
    confirmed = auth_client.post(f"/api/v1/lab-uploads/{created['id']}/confirm/", {}, format="json")
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed.json()["status"] == "SCANNING"

    scan_lab_upload.run(str(upload.id))
    upload.refresh_from_db()
    assert upload.status == LabUploadStatus.CLEAN

    listing = auth_client.get(f"/api/v1/lab-uploads/?assessment_id={assessment.id}")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    download = auth_client.get(f"/api/v1/lab-uploads/{upload.id}/download/")
    assert download.status_code == 200
    assert download.json()["url"]
    assert AuditLog.objects.filter(action="lab_upload.downloaded").exists()


def test_confirm_missing_object_is_rejected(auth_client: APIClient, patient_user) -> None:
    assessment = _paid_assessment(auth_client, patient_user)
    created = _start_upload(auth_client, str(assessment.id))
    response = auth_client.post(f"/api/v1/lab-uploads/{created['id']}/confirm/", {}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert "UPLOAD_MISSING" in response.text


def test_confirm_invalid_file_type_is_rejected(auth_client: APIClient, patient_user) -> None:
    assessment = _paid_assessment(auth_client, patient_user)
    content = b"not a real pdf"
    created = _start_upload(auth_client, str(assessment.id), content)
    upload = LabUpload.objects.get(pk=created["id"])
    _put_object(upload.storage_key, content)
    response = auth_client.post(f"/api/v1/lab-uploads/{created['id']}/confirm/", {}, format="json")
    assert response.status_code == 400
    assert "INVALID_FILE_TYPE" in response.text


def test_upload_limit_and_delete(auth_client: APIClient, patient_user) -> None:
    assessment = _paid_assessment(auth_client, patient_user)
    for index in range(5):
        _start_upload(auth_client, str(assessment.id), b"%PDF-" + str(index).encode())
    extra = auth_client.post(
        "/api/v1/lab-uploads/",
        {
            "assessment_id": str(assessment.id),
            "file_name": "extra.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
        },
        format="json",
    )
    assert extra.status_code == 409
    assert extra.json()["code"] == "upload_limit_reached"

    upload = LabUpload.objects.filter(assessment=assessment).first()
    assert upload is not None
    deleted = auth_client.delete(f"/api/v1/lab-uploads/{upload.id}/")
    assert deleted.status_code == 204
    upload.refresh_from_db()
    assert upload.is_deleted is True


def test_submit_labs_requires_files(auth_client: APIClient, patient_user) -> None:
    assessment = _paid_assessment(auth_client, patient_user)
    response = auth_client.post(
        f"/api/v1/assessments/{assessment.id}/labs/submit/", {}, format="json"
    )
    assert response.status_code == 409
    assert response.json()["code"] == "no_lab_files"


def test_skip_and_submit_labs(auth_client: APIClient, patient_user) -> None:
    assessment = _paid_assessment(auth_client, patient_user)
    skipped = auth_client.post(f"/api/v1/assessments/{assessment.id}/labs/skip/", {}, format="json")
    assert skipped.status_code == 200
    assert skipped.json() == {"labs_status": "DEFERRED"}
    again = auth_client.post(f"/api/v1/assessments/{assessment.id}/labs/skip/", {}, format="json")
    assert again.status_code == 409

    upload = LabUpload.objects.create(
        assessment=assessment,
        uploaded_by=patient_user,
        file_name="BloodTest.pdf",
        storage_key=f"labs/{assessment.id}/manual.pdf",
        content_type="application/pdf",
        size_bytes=10,
        status=LabUploadStatus.CLEAN,
    )
    submitted = auth_client.post(
        f"/api/v1/assessments/{assessment.id}/labs/submit/", {}, format="json"
    )
    assert submitted.status_code == 200
    assert submitted.json() == {
        "labs_status": "SUBMITTED",
        "evaluation_status": "SUBMITTED",
    }
    upload.refresh_from_db()
    assessment.refresh_from_db()
    assert assessment.labs_status == "SUBMITTED"


# --------------------------------------------------------------------------- #
# Evaluation and prescription
# --------------------------------------------------------------------------- #


def test_evaluation_not_found_for_draft(auth_client: APIClient, patient_user) -> None:
    _status, created = start_assessment(auth_client, patient_user)
    assert auth_client.get(f"/api/v1/assessments/{created['id']}/evaluation/").status_code == 404


def test_evaluation_view_and_non_owner_audit(
    auth_client: APIClient, patient_user, make_auth_client, admin_user
) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    response = auth_client.get(f"/api/v1/assessments/{assessment.id}/evaluation/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "AWAITING_PAYMENT"
    assert [item["key"] for item in payload["checklist"]] == [
        "QUESTIONNAIRE",
        "LABS",
        "PHYSICIAN_REVIEW",
    ]
    assert payload["estimated_review_hours"] == {"min": 24, "max": 48}
    assert payload["can_upload_labs"] is True

    admin_client = make_auth_client(admin_user)
    assert admin_client.get(f"/api/v1/assessments/{assessment.id}/evaluation/").status_code == 200
    assert AuditLog.objects.filter(actor=admin_user, action="evaluation.read").exists()


def test_physician_cannot_use_patient_endpoints(
    auth_client: APIClient,
    patient_user,
    make_auth_client,
) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    physician_client = make_auth_client(PhysicianFactory())
    response = physician_client.post(
        f"/api/v1/assessments/{assessment.id}/fee/pay/",
        {"payment_method": "CULQI", "provider_token": "tok"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
    )
    assert response.status_code == 403


def test_prescription_download(auth_client: APIClient, patient_user) -> None:
    assessment, _result = submitted_assessment(auth_client, patient_user)
    evaluation = Evaluation.objects.get(assessment=assessment)
    evaluation.status = EvaluationStatus.APPROVED
    evaluation.decided_at = evaluation.submitted_at
    evaluation.save(update_fields=["status", "decided_at"])
    Prescription.objects.create(
        evaluation=evaluation,
        medication_name="Semaglutide GLP-1",
        dosage_label="0.5 mg SubQ Once Weekly",
        pdf_key=f"prescriptions/{evaluation.id}.pdf",
    )
    _put_object(f"prescriptions/{evaluation.id}.pdf", b"%PDF-1.7 prescription")
    response = auth_client.get(
        f"/api/v1/assessments/{assessment.id}/evaluation/prescription/download/"
    )
    assert response.status_code == 200
    assert response.json()["url"]
    assert AuditLog.objects.filter(action="prescription.downloaded").exists()
