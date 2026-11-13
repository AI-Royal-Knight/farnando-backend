"""Part 5 sections 3 to 5: dashboards, patient management and evaluation assignment."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.assessments.models import Evaluation, EvaluationReviewRequest, Program
from apps.clinical.models import Specialty
from apps.core.models import AuditLog
from apps.notifications.models import Notification
from apps.orders.models import Subscription
from tests.factories.part3 import (
    idem,
    make_evaluation,
    make_physician,
    make_treatment,
    program,
)
from tests.factories.part5 import make_staff
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db

EVALS = "/api/v1/staff/evaluations/"
PATIENTS = "/api/v1/staff/patients/"


@pytest.fixture
def ops(make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(make_staff("OPERATIONS", name="Olga Ops"))


@pytest.fixture
def support(make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(make_staff("SUPPORT_AGENT", name="Sam Support"))


@pytest.fixture
def nurse(make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(make_staff("NURSE", name="Nora Nurse"))


def submitted_evaluation(**kwargs: Any) -> Evaluation:
    _, evaluation = make_evaluation(PatientFactory(), "SUBMITTED", with_measurement=True, **kwargs)
    assert evaluation is not None
    return evaluation


def post(client: APIClient, url: str, body: dict[str, Any], version: int | None = None) -> Any:
    headers: dict[str, str] = {"HTTP_IDEMPOTENCY_KEY": idem()}
    if version is not None:
        headers["HTTP_IF_MATCH"] = f'"{version}"'
    return client.post(url, body, format="json", **headers)


# --- Evaluations (5) ---------------------------------------------------------------


def test_evaluation_list_counts_and_no_health_details(support: APIClient, nurse: APIClient) -> None:
    doctor = make_physician()
    pending = submitted_evaluation()
    make_evaluation(PatientFactory(), "APPROVED", physician=doctor)
    make_evaluation(PatientFactory(), "PENDING_LABS", physician=doctor)
    body = support.get(EVALS).json()
    assert body["counts"] == {"pending": 1, "approved": 1, "not_suitable": 0, "awaiting_labs": 1}
    assert body["count"] == 3
    pending_only = support.get(f"{EVALS}?status_group=PENDING&assignment=UNASSIGNED").json()
    item = pending_only["results"][0]
    assert item["id"] == str(pending.pk)
    assert item["questionnaire_state"] == "COMPLETE"
    assert item["bmi"] == 26.8 and item["bmi_category"] == "OVERWEIGHT"
    assert item["recommended_physician"]["id"] == str(doctor.pk)
    assert "answers" not in item and "questionnaire" not in item
    assert support.get(f"{EVALS}?status_group=NOPE").status_code == 400
    assert support.get(f"{EVALS}?from=bad").status_code == 400

    plain = support.get(f"{EVALS}{pending.pk}/").json()
    assert "questionnaire" not in plain and plain["version"] == pending.version
    clinical = nurse.get(f"{EVALS}{pending.pk}/").json()
    assert "questionnaire" in clinical and "internal_note" not in clinical
    assert AuditLog.objects.filter(object_id=str(pending.pk), action_type="READ").exists()


def test_urgent_priority_by_age_and_by_staff(support: APIClient) -> None:
    evaluation = submitted_evaluation()
    Evaluation.objects.filter(pk=evaluation.pk).update(
        submitted_at=timezone.now() - timedelta(hours=30)
    )
    assert support.get(f"{EVALS}?priority=URGENT").json()["count"] == 1
    changed = support.post(
        f"{EVALS}{evaluation.pk}/priority/", {"priority": "NORMAL"}, format="json"
    )
    assert changed.status_code == 200 and changed.json()["priority"] == "NORMAL"
    assert support.get(f"{EVALS}?priority=URGENT").json()["count"] == 0


def test_assign_reserve_reassign_and_unassign(
    support: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    first = make_physician(name="First Doc")
    second = make_physician(name="Second Doc")
    evaluation = submitted_evaluation()
    url = f"{EVALS}{evaluation.pk}/"

    recommended = support.get(f"{url}recommended-physicians/").json()
    assert {row["physician"]["id"] for row in recommended} == {str(first.pk), str(second.pk)}
    assert 0 <= recommended[0]["score"] <= 100 and recommended[0]["reasons"]

    missing = support.post(f"{url}assign/", {"physician_id": str(first.pk)}, format="json")
    assert missing.status_code == 428
    stale = post(support, f"{url}assign/", {"physician_id": str(first.pk)}, version=99)
    assert stale.status_code == 412
    assigned = post(support, f"{url}assign/", {"physician_id": str(first.pk)}, evaluation.version)
    assert assigned.status_code == 200, assigned.json()
    assert assigned.json()["assigned_physician"]["id"] == str(first.pk)
    assert Notification.objects.filter(user=first, type="NEW_EVALUATION_IN_QUEUE").exists()

    # B5: only the reserved physician sees it and others get 409 assigned_to_other.
    other_queue = make_auth_client(second).get("/api/v1/clinical/evaluations/").json()
    assert other_queue["count"] == 0
    blocked = make_auth_client(second).post(f"/api/v1/clinical/evaluations/{evaluation.pk}/claim/")
    assert blocked.status_code == 409 and blocked.json()["code"] == "assigned_to_other"
    claimed = make_auth_client(first).post(f"/api/v1/clinical/evaluations/{evaluation.pk}/claim/")
    assert claimed.status_code == 200

    evaluation.refresh_from_db()
    moved = post(support, f"{url}assign/", {"physician_id": str(second.pk)}, evaluation.version)
    assert moved.status_code == 200
    evaluation.refresh_from_db()
    assert evaluation.status == "SUBMITTED" and evaluation.physician_id is None
    assert evaluation.assigned_physician_id == second.pk

    unassigned = post(support, f"{url}unassign/", {"reason": "Rebalance"}, evaluation.version)
    assert unassigned.status_code == 200
    assert unassigned.json()["assigned_physician"] is None

    unaccepting = make_physician(name="Busy Doc", accepts=False)
    evaluation.refresh_from_db()
    refused = post(
        support, f"{url}assign/", {"physician_id": str(unaccepting.pk)}, evaluation.version
    )
    assert refused.status_code == 409 and refused.json()["code"] == "physician_not_eligible"


def test_request_labs_and_reopen(ops: APIClient, support: APIClient) -> None:
    evaluation = submitted_evaluation()
    url = f"{EVALS}{evaluation.pk}/request-labs/"
    denied = post(support, url, {"panels": ["CMP"]}, evaluation.version)
    assert denied.status_code == 403
    assert denied.json()["details"]["required"] == "EVALUATIONS_REQUEST_LABS"
    unknown = post(ops, url, {"panels": ["NOPE"]}, evaluation.version)
    assert unknown.status_code == 400
    done = post(ops, url, {"panels": ["CMP"], "message": "Fasting please"}, evaluation.version)
    assert done.status_code == 200, done.json()
    assert done.json()["status"] == "PENDING_LABS"
    evaluation.refresh_from_db()
    assert evaluation.lab_recommendations[0]["code"] == "CMP"

    screened = submitted_evaluation()
    Evaluation.objects.filter(pk=screened.pk).update(status="SCREENED_OUT")
    EvaluationReviewRequest.objects.create(evaluation=screened, note="Please review")
    listing = support.get(f"{EVALS}?status_group=SCREENED_OUT&review_requested=true").json()
    assert listing["count"] == 1
    screened.refresh_from_db()
    no_note = post(support, f"{EVALS}{screened.pk}/reopen/", {}, screened.version)
    assert no_note.status_code == 400
    Program.objects.filter(pk=program().pk).update(fee_is_waived=True)
    reopened = post(
        support, f"{EVALS}{screened.pk}/reopen/", {"note": "Human review ok"}, screened.version
    )
    assert reopened.status_code == 200, reopened.json()
    assert reopened.json()["status"] == "SUBMITTED"


# --- Patients (4) ------------------------------------------------------------------


def test_patient_list_detail_assign_and_suspend(
    admin_client: APIClient,
    support: APIClient,
    nurse: APIClient,
    make_auth_client: Callable[[User], APIClient],
) -> None:
    doctor = make_physician()
    active = PatientFactory(full_name="Ana Activa")
    _, evaluation = make_evaluation(active, "APPROVED", physician=doctor)
    make_treatment(active, doctor, evaluation=evaluation)
    pending = PatientFactory(full_name="Pedro Pendiente")
    make_evaluation(pending, "SUBMITTED")

    listing = support.get(PATIENTS).json()
    assert listing["summary"]["total"] == 2
    assert listing["summary"]["active"] == 1 and listing["summary"]["pending"] == 1
    rows = {row["name"]: row for row in listing["results"]}
    assert rows["Ana Activa"]["status"] == "ACTIVE"
    assert rows["Ana Activa"]["assigned_physician"]["id"] == str(doctor.pk)
    assert support.get(f"{PATIENTS}?status=ACTIVE").json()["count"] == 1
    assert support.get(f"{PATIENTS}?search=pedro").json()["count"] == 1
    assert support.get(f"{PATIENTS}?ordering=bogus").status_code == 400

    detail = support.get(f"{PATIENTS}{active.pk}/").json()
    assert detail["clinical"] is None
    assert len(detail["stats"]) == 4
    assert detail["plan"]["program"]["name"]
    clinical = nurse.get(f"{PATIENTS}{active.pk}/").json()
    assert clinical["clinical"]["prescribing_physician"]["id"] == str(doctor.pk)
    assert AuditLog.objects.filter(action="patient.viewed", object_id=str(active.pk)).count() == 2

    second = make_physician(name="Second Doc")
    assigned = support.post(
        f"{PATIENTS}{pending.pk}/assign-physician/",
        {"physician_id": str(second.pk), "reason": "Closer"},
        format="json",
    )
    assert assigned.status_code == 200, assigned.json()
    assert assigned.json()["assigned_physician"]["id"] == str(second.pk)
    assert Notification.objects.filter(user=second, type="PATIENT_ASSIGNED").exists()
    my_patients = make_auth_client(second).get("/api/v1/clinical/patients/").json()
    assert any(row["patient"]["id"] == str(pending.pk) for row in my_patients["results"])

    forbidden = support.post(f"{PATIENTS}{active.pk}/suspend/", {"reason": "Fraud"}, format="json")
    assert forbidden.status_code == 403
    patient_client = make_auth_client(active)
    suspended = admin_client.post(
        f"{PATIENTS}{active.pk}/suspend/", {"reason": "Chargeback fraud"}, format="json"
    )
    assert suspended.status_code == 200 and suspended.json()["status"] == "SUSPENDED"
    assert patient_client.get("/api/v1/patients/me/").status_code == 401
    assert admin_client.post(f"{PATIENTS}{active.pk}/reactivate/").json()["status"] == "ACTIVE"
    assert support.get(f"{PATIENTS}{doctor.pk}/").status_code == 404


def test_dashboards(
    admin_client: APIClient, support: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    submitted_evaluation()
    staff = support.get("/api/v1/staff/dashboard/").json()
    assert staff["stats"]["unassigned_evaluations_count"] == 1
    assert staff["stats"]["orders_processing_count"] == 0
    assert len(staff["unassigned_evaluations"]) == 1
    assert staff["urgent_tickets"] == []

    admin = admin_client.get("/api/v1/admin/dashboard/").json()
    assert admin["stats"]["open_tickets_count"] == 0
    assert set(admin["kpis"]) == {
        "total_revenue",
        "monthly_recurring_revenue",
        "total_orders",
        "total_users",
    }
    assert admin["open_privacy_requests_count"] == 0
    assert support.get("/api/v1/admin/dashboard/").status_code == 403


def test_specialty_restricted_queue(make_auth_client: Callable[[User], APIClient]) -> None:
    endocrine = Specialty.objects.get(name="Endocrinology")
    general = Specialty.objects.get(name="General Medicine")
    prog = program()
    prog.allowed_specialties.set([endocrine])
    insider = make_physician(name="Endo Doc", specialty_ref=endocrine)
    outsider = make_physician(name="GP Doc", specialty_ref=general)
    submitted_evaluation()
    assert make_auth_client(insider).get("/api/v1/clinical/evaluations/").json()["count"] == 1
    assert make_auth_client(outsider).get("/api/v1/clinical/evaluations/").json()["count"] == 0
    assert Subscription.objects.count() == 0


def test_physician_directory_for_assignment(
    support: APIClient, make_auth_client: Callable[[User], APIClient]
) -> None:
    first = make_physician(name="Ana Doc")
    second = make_physician(name="Beto Doc")

    response = support.get("/api/v1/staff/physicians/")
    assert response.status_code == 200, response.json()
    rows = response.json()
    assert [row["physician"]["id"] for row in rows] == [str(first.pk), str(second.pk)]
    assert all(row["queue_size"] == 0 for row in rows)
    assert {"specialty", "accepting_new_consultations"} <= set(rows[0])

    patient = make_auth_client(PatientFactory())
    assert patient.get("/api/v1/staff/physicians/").status_code == 403
