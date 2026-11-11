"""Contract Part 4 sections 3, 5, 6, 7 and 8: care workflows on the clinician side."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import ClinicalNote
from apps.clinical.treatments import apply_due_versions
from apps.consultations.models import AvailabilityRule, Consultation
from apps.core.models import AuditLog
from apps.notifications.models import Notification
from apps.orders.models import Order, Subscription
from apps.patients.models import PatientProfile
from apps.treatments.models import Treatment, TreatmentRenewalRequest
from tests.factories.part3 import (
    consultation_type,
    give_consents,
    idem,
    make_consultation,
    make_evaluation,
    make_nurse,
    make_physician,
    make_treatment,
    package,
)
from tests.factories.users import PatientFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def physician(db: Any) -> User:
    return make_physician()


@pytest.fixture
def doctor(physician: User, make_auth_client: Callable[[User], APIClient]) -> APIClient:
    return make_auth_client(physician)


@pytest.fixture
def patient(physician: User) -> User:
    user = PatientFactory(full_name="Sarah Jenkins")
    give_consents(user)
    _assessment, evaluation = make_evaluation(
        user, "APPROVED", physician=physician, with_measurement=True
    )
    assert evaluation is not None
    evaluation.decided_at = timezone.now()
    evaluation.save(update_fields=["decided_at"])
    return user


@pytest.fixture
def treatment(patient: User, physician: User) -> Treatment:
    return make_treatment(patient, physician, dose_label="0.5 mg")


def post(client: APIClient, url: str, body: dict[str, Any] | None = None) -> Any:
    return client.post(url, body or {}, format="json", HTTP_IDEMPOTENCY_KEY=idem())


# --- My Patients, chart and notes ------------------------------------------------


def test_my_patients_and_chart(
    doctor: APIClient, physician: User, patient: User, treatment: Treatment
) -> None:
    stranger = PatientFactory(full_name="Not Mine")
    make_evaluation(stranger, "APPROVED", physician=make_physician(name="Other"))
    rows = doctor.get("/api/v1/clinical/patients/").json()
    assert rows["count"] == 1
    row = rows["results"][0]
    assert row["patient"]["name"] == "Sarah Jenkins"
    assert row["status"] == "NEWLY_APPROVED"
    assert row["medication_label"] == "Semaglutide GLP-1 0.5 mg SubQ"
    assert row["program"]["name"] == "Weight Loss"
    assert doctor.get("/api/v1/clinical/patients/?status=PAUSED").json()["count"] == 0
    assert doctor.get("/api/v1/clinical/patients/?search=jenk").json()["count"] == 1
    assert doctor.get("/api/v1/clinical/patients/?status=NOPE").status_code == 400

    chart = doctor.get(f"/api/v1/clinical/patients/{patient.pk}/")
    assert chart.status_code == 200
    body = chart.json()
    assert body["patient"]["email"] == patient.email
    assert body["latest_biometrics"]["bmi"] == 26.8
    assert body["treatments"][0]["id"] == str(treatment.pk)
    assert body["notes_count"] == 0
    assert AuditLog.objects.filter(action="patient_chart.read").exists()
    assert doctor.get(f"/api/v1/clinical/patients/{stranger.pk}/").status_code == 404


def test_consultation_only_physician_sees_no_contact(
    patient: User, make_auth_client: Callable[[User], APIClient]
) -> None:
    other = make_physician(name="Video Only")
    make_consultation(patient, other, start_in=timedelta(days=2))
    body = make_auth_client(other).get(f"/api/v1/clinical/patients/{patient.pk}/").json()
    assert body["patient"]["email"] is None and body["patient"]["phone"] is None


def test_notes(doctor: APIClient, patient: User) -> None:
    url = f"/api/v1/clinical/patients/{patient.pk}/notes/"
    created = doctor.post(url, {"kind": "CLINICAL", "body": "Tolerating dose well."}, format="json")
    assert created.status_code == 201
    assert created.json()["author"]["name"] == "Dr. Marcus Vance, MD"
    doctor.post(url, {"kind": "ADMINISTRATIVE", "body": "Called patient."}, format="json")
    notes = doctor.get(url).json()
    assert [item["kind"] for item in notes["results"]] == ["ADMINISTRATIVE", "CLINICAL"]
    assert doctor.post(url, {"kind": "CLINICAL", "body": ""}, format="json").status_code == 400
    assert doctor.patch(url, {}, format="json").status_code == 405


# --- Treatments -----------------------------------------------------------------


def test_adjust_now_and_later(doctor: APIClient, patient: User, treatment: Treatment) -> None:
    url = f"/api/v1/clinical/treatments/{treatment.pk}/adjust/"
    out = post(doctor, url, {"dose": {"value": "3", "unit": "mg"}, "reason": "Too high a dose."})
    assert out.status_code == 400 and out.json()["code"] == "dose_out_of_range"
    response = post(
        doctor,
        url,
        {
            "dose": {"value": "1.0", "unit": "mg"},
            "reason": "Week 8 titration step.",
            "patient_note": "Increase this week.",
        },
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["current_dose"]["label"] == "1 mg"
    assert body["versions"][-1]["reason"] == "Week 8 titration step."
    assert Notification.objects.filter(user=patient, type="TREATMENT_UPDATED").exists()

    later = (timezone.localdate() + timedelta(days=3)).isoformat()
    scheduled = post(
        doctor,
        url,
        {
            "dose": {"value": "1.7", "unit": "mg"},
            "reason": "Next titration step.",
            "effective_from": later,
        },
    )
    assert scheduled.json()["current_dose"]["label"] == "1 mg"
    assert apply_due_versions(today=timezone.localdate() + timedelta(days=3)) == 1
    treatment.refresh_from_db()
    assert treatment.dose_label == "1.7 mg"
    past = (timezone.localdate() - timedelta(days=1)).isoformat()
    assert (
        post(
            doctor,
            url,
            {
                "dose": {"value": "1.7", "unit": "mg"},
                "reason": "Backdated change.",
                "effective_from": past,
            },
        ).status_code
        == 400
    )


def test_pause_resume_discontinue(doctor: APIClient, patient: User, treatment: Treatment) -> None:
    base = f"/api/v1/clinical/treatments/{treatment.pk}"
    reason = {"reason": "Patient reported nausea."}
    assert post(doctor, f"{base}/resume/", reason).json()["code"] == "invalid_state_transition"
    assert post(doctor, f"{base}/pause/", reason).json()["status"] == "PAUSED"
    assert post(doctor, f"{base}/resume/", reason).json()["status"] == "ACTIVE"

    order = Order.objects.create(
        user=patient,
        treatment=treatment,
        package=package(),
        number="ADL-ORD-7777",
        status="PENDING_PAYMENT",
        placed_at=timezone.now(),
        subtotal="1",
        shipping="0",
        tax="0",
        total="1",
    )
    subscription = Subscription.objects.create(
        user=patient,
        package=package("Monthly Subscription"),
        treatment=treatment,
        current_period_start=timezone.now(),
        current_period_end=timezone.now() + timedelta(days=30),
    )
    stopped = post(doctor, f"{base}/discontinue/", reason)
    assert stopped.json()["status"] == "DISCONTINUED"
    order.refresh_from_db()
    subscription.refresh_from_db()
    assert order.status == "CANCELLED"
    assert subscription.needs_staff_review is True
    assert post(doctor, f"{base}/pause/", reason).json()["code"] == "invalid_state_transition"
    assert post(doctor, f"{base}/pause/", {"reason": "short"}).status_code == 400


def test_treatment_requires_care_relationship(
    treatment: Treatment, make_auth_client: Callable[[User], APIClient]
) -> None:
    other = make_auth_client(make_physician(name="Stranger"))
    response = post(
        other, f"/api/v1/clinical/treatments/{treatment.pk}/pause/", {"reason": "No relation here."}
    )
    assert response.status_code == 404


def test_renewals(
    doctor: APIClient,
    physician: User,
    patient: User,
    treatment: Treatment,
    make_auth_client: Callable[[User], APIClient],
) -> None:
    patient_client = make_auth_client(patient)
    patient_client.post(
        f"/api/v1/treatments/{treatment.pk}/renewal-requests/",
        {"note": "Running out"},
        format="json",
    )
    assert Notification.objects.filter(user=physician, type="RENEWAL_REQUESTED").exists()
    listing = doctor.get("/api/v1/clinical/renewal-requests/").json()
    assert listing["count"] == 1
    item = listing["results"][0]
    assert item["patient_note"] == "Running out"
    assert item["treatment"]["refills"] == {"remaining": 2, "total": 2}
    too_many = post(
        doctor, f"/api/v1/clinical/renewal-requests/{item['id']}/approve/", {"refills_added": 9}
    )
    assert too_many.status_code == 400
    approved = post(
        doctor,
        f"/api/v1/clinical/renewal-requests/{item['id']}/approve/",
        {"refills_added": 2, "note": "Approved for 2 more"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"
    assert approved.json()["treatment"]["refills"] == {"remaining": 4, "total": 4}
    assert (
        post(
            doctor,
            f"/api/v1/clinical/renewal-requests/{item['id']}/deny/",
            {"reason": "Changed my mind."},
        ).json()["code"]
        == "invalid_state_transition"
    )
    second = TreatmentRenewalRequest.objects.create(treatment=treatment)
    denied = post(
        doctor,
        f"/api/v1/clinical/renewal-requests/{second.pk}/deny/",
        {"reason": "Needs a follow-up visit first.", "patient_note": "Please book a visit."},
    )
    assert denied.json()["status"] == "DENIED"
    assert doctor.get("/api/v1/clinical/renewal-requests/?status=DENIED").json()["count"] == 1


# --- Consultations ----------------------------------------------------------------


def test_clinician_consultation_flow(doctor: APIClient, physician: User, patient: User) -> None:
    requested = make_consultation(
        patient, physician, start_in=timedelta(days=2), status="REQUESTED"
    )
    listing = doctor.get("/api/v1/clinical/consultations/?scope=UPCOMING").json()
    assert listing["meta"]["requested_count"] == 1
    item = listing["results"][0]
    assert item["can_accept"] is True and item["mode"] == "TELEHEALTH"
    assert item["patient"]["name"] == "Sarah Jenkins"

    accepted = post(doctor, f"/api/v1/clinical/consultations/{requested.pk}/accept/")
    assert accepted.json()["status"] == "CONFIRMED"
    assert Notification.objects.filter(user=patient, type="CONSULTATION_CONFIRMED").exists()

    agenda = doctor.patch(
        f"/api/v1/clinical/consultations/{requested.pk}/",
        {"agenda": "Dose tolerance check"},
        format="json",
    )
    assert agenda.json()["agenda"] == "Dose tolerance check"
    assert agenda.json()["reason"] == "Dose tolerance check"

    current = Consultation.objects.get(pk=requested.pk)
    from apps.consultations.availability import compute_slots

    day = timezone.localdate() + timedelta(days=4)
    free = compute_slots(physician, current.consultation_type, day, day, apply_lead_time=False)
    new_start = free[0].start.isoformat().replace("+00:00", "Z")
    moved = doctor.post(
        f"/api/v1/clinical/consultations/{requested.pk}/reschedule/",
        {"start": new_start, "reason": "Clinic emergency"},
        format="json",
    )
    assert moved.status_code == 200, moved.json()
    assert moved.json()["status"] == "CONFIRMED"
    current.refresh_from_db()
    assert current.rescheduled_by_physician_at is not None

    cancelled = doctor.post(
        f"/api/v1/clinical/consultations/{requested.pk}/cancel/",
        {"reason": "Unwell"},
        format="json",
    )
    assert cancelled.json()["status"] == "CANCELLED"
    current.refresh_from_db()
    assert current.cancelled_by == physician


def test_decline(doctor: APIClient, physician: User, patient: User) -> None:
    requested = make_consultation(
        patient, physician, start_in=timedelta(days=2), status="REQUESTED"
    )
    assert (
        doctor.post(
            f"/api/v1/clinical/consultations/{requested.pk}/decline/", {}, format="json"
        ).status_code
        == 400
    )
    declined = doctor.post(
        f"/api/v1/clinical/consultations/{requested.pk}/decline/",
        {"reason": "Fully booked", "patient_note": "Please pick another day"},
        format="json",
    )
    assert declined.json()["status"] == "DECLINED"
    assert Notification.objects.filter(user=patient, type="CONSULTATION_DECLINED").exists()


def test_join_complete_and_notes(doctor: APIClient, physician: User, patient: User) -> None:
    visit = make_consultation(patient, physician, start_in=timedelta(minutes=3))
    early = post(
        doctor, f"/api/v1/clinical/consultations/{visit.pk}/complete/", {"clinical_notes": "x"}
    )
    assert early.json()["code"] == "invalid_state_transition"
    joined = doctor.post(f"/api/v1/clinical/consultations/{visit.pk}/join/")
    assert joined.status_code == 200
    assert joined.json()["token"].startswith("fake_owner_")
    assert joined.json()["consultation"]["status"] == "IN_PROGRESS"
    done = post(
        doctor,
        f"/api/v1/clinical/consultations/{visit.pk}/complete/",
        {
            "clinical_notes": "Stable, continue 0.5 mg.",
            "visit_summary": "Keep the same dose.",
            "follow_up": {
                "consultation_type_id": str(consultation_type("FOLLOW_UP").pk),
                "suggested_in_days": 30,
            },
        },
    )
    assert done.status_code == 200, done.json()
    body = done.json()
    assert body["status"] == "COMPLETED"
    assert body["clinical_notes"] == "Stable, continue 0.5 mg."
    assert body["follow_up"]["suggested_in_days"] == 30
    assert ClinicalNote.objects.filter(consultation=visit, kind="CLINICAL").exists()


def test_no_show(doctor: APIClient, physician: User, patient: User) -> None:
    upcoming = make_consultation(patient, physician, start_in=timedelta(minutes=5))
    too_soon = doctor.post(
        f"/api/v1/clinical/consultations/{upcoming.pk}/no-show/", {}, format="json"
    )
    assert too_soon.status_code == 409
    missed = make_consultation(
        patient, physician, start_in=-timedelta(minutes=20), code="FOLLOW_UP"
    )
    marked = doctor.post(
        f"/api/v1/clinical/consultations/{missed.pk}/no-show/", {"note": "No answer"}, format="json"
    )
    assert marked.json()["status"] == "NO_SHOW"


def test_other_physicians_consultation_is_404(
    patient: User, make_auth_client: Callable[[User], APIClient], physician: User
) -> None:
    visit = make_consultation(patient, physician, start_in=timedelta(days=2), status="REQUESTED")
    other = make_auth_client(make_physician(name="Other"))
    assert other.get(f"/api/v1/clinical/consultations/{visit.pk}/").status_code == 404


def test_patient_actions_notify_physician(
    patient: User, physician: User, make_auth_client: Callable[[User], APIClient]
) -> None:
    visit = make_consultation(patient, physician, start_in=timedelta(days=3), status="REQUESTED")
    make_auth_client(patient).post(f"/api/v1/consultations/{visit.pk}/cancel/", {}, format="json")
    assert Notification.objects.filter(
        user=physician, type="CONSULTATION_CANCELLED_BY_PATIENT"
    ).exists()


# --- Availability, calendar and dashboard -------------------------------------------


def test_availability_rules(make_auth_client: Callable[[User], APIClient], patient: User) -> None:
    physician = make_physician(name="Fresh Calendar")
    AvailabilityRule.objects.filter(physician=physician).delete()
    client = make_auth_client(physician)
    url = "/api/v1/physicians/me/availability-rules/"
    rule = client.post(
        url,
        {"kind": "AVAILABLE", "day_of_week": 1, "start_time": "09:00", "end_time": "18:00"},
        format="json",
    )
    assert rule.status_code == 201, rule.json()
    assert rule.json()["start_time"] == "09:00"
    assert rule.json()["consultation_type_ids"] is None
    overlap = client.post(
        url,
        {"kind": "AVAILABLE", "day_of_week": 1, "start_time": "17:00", "end_time": "19:00"},
        format="json",
    )
    assert overlap.status_code == 409 and overlap.json()["code"] == "overlapping_rule"
    step = client.post(
        url,
        {"kind": "AVAILABLE", "day_of_week": 2, "start_time": "09:10", "end_time": "10:00"},
        format="json",
    )
    assert step.status_code == 400
    lunch = client.post(
        url,
        {
            "kind": "BLOCKED",
            "day_of_week": 1,
            "start_time": "13:00",
            "end_time": "14:00",
            "label": "Lunch break",
        },
        format="json",
    )
    assert lunch.status_code == 201

    # Book a Monday 10:00 Lima slot, then try to block it.
    today = timezone.localdate()
    monday = today + timedelta(days=(8 - today.isoweekday()) % 7 + 7)
    assert monday.isoweekday() == 1
    start = datetime.combine(monday, time(15, 0), tzinfo=UTC)
    Consultation.objects.create(
        patient=patient,
        physician=physician,
        consultation_type=consultation_type(),
        status="CONFIRMED",
        start=start,
        end=start + timedelta(minutes=30),
    )
    clash = client.post(
        url,
        {"kind": "BLOCKED", "day_of_week": 1, "start_time": "10:00", "end_time": "11:00"},
        format="json",
    )
    assert clash.status_code == 409 and clash.json()["code"] == "conflicts_with_consultations"
    assert len(clash.json()["details"]["consultation_ids"]) == 1
    shrink = client.patch(f"{url}{rule.json()['id']}/", {"end_time": "10:00"}, format="json")
    assert shrink.status_code == 409
    assert client.delete(f"{url}{lunch.json()['id']}/").status_code == 204
    assert len(client.get(url).json()) == 1

    calendar = client.get(
        "/api/v1/physicians/me/calendar/", {"from": monday.isoformat(), "to": monday.isoformat()}
    )
    # Unverified status does not matter here: make_physician creates verified doctors.
    assert calendar.status_code == 200
    types = [item["type"] for item in calendar.json()["items"]]
    assert types == ["AVAILABLE_SLOT", "CONSULTATION", "AVAILABLE_SLOT"]
    assert (
        client.get(
            "/api/v1/physicians/me/calendar/", {"from": "2026-11-01", "to": "2026-12-30"}
        ).status_code
        == 400
    )


def test_time_blocks_and_extra_availability(
    make_auth_client: Callable[[User], APIClient],
) -> None:
    physician = make_physician(name="Weekend Doc")
    AvailabilityRule.objects.filter(physician=physician).delete()
    client = make_auth_client(physician)
    day = timezone.localdate() + timedelta(days=5)
    start = datetime.combine(day, time(14, 0), tzinfo=UTC)
    extra = client.post(
        "/api/v1/physicians/me/time-blocks/",
        {
            "kind": "EXTRA_AVAILABLE",
            "start": start.isoformat(),
            "end": (start + timedelta(hours=2)).isoformat(),
        },
        format="json",
    )
    assert extra.status_code == 201, extra.json()
    too_long = client.post(
        "/api/v1/physicians/me/time-blocks/",
        {
            "kind": "EXTRA_AVAILABLE",
            "start": start.isoformat(),
            "end": (start + timedelta(hours=30)).isoformat(),
        },
        format="json",
    )
    assert too_long.status_code == 400
    slots = APIClient()
    patient = PatientFactory()
    from rest_framework_simplejwt.tokens import RefreshToken

    slots.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(patient).access_token}")
    available = slots.get(
        f"/api/v1/physicians/{physician.pk}/availability/",
        {
            "from": day.isoformat(),
            "to": day.isoformat(),
            "consultation_type_id": consultation_type().pk,
        },
    ).json()["slots"]
    assert len(available) == 4
    listing = client.get("/api/v1/physicians/me/time-blocks/").json()
    assert listing[0]["kind"] == "EXTRA_AVAILABLE"
    assert (
        client.delete(f"/api/v1/physicians/me/time-blocks/{extra.json()['id']}/").status_code == 204
    )


def test_dashboard(doctor: APIClient, physician: User, patient: User, treatment: Treatment) -> None:
    make_consultation(patient, physician, start_in=timedelta(minutes=30))
    TreatmentRenewalRequest.objects.create(treatment=treatment)
    body = doctor.get("/api/v1/physicians/me/dashboard/").json()
    assert body["in_treatment_count"] == 1
    assert body["pending_renewal_requests_count"] == 1
    assert body["consultations_this_week_count"] >= 1
    assert body["awaiting_evaluation_count"] == 0
    assert isinstance(body["today_consultations"], list)


# --- Clinician messaging ----------------------------------------------------------------


def test_clinician_messaging(
    doctor: APIClient,
    physician: User,
    patient: User,
    treatment: Treatment,
    make_auth_client: Callable[[User], APIClient],
) -> None:
    created = doctor.post(
        "/api/v1/message-threads/",
        {"participant_id": str(patient.pk), "body": "How are you feeling?"},
        format="json",
    )
    assert created.status_code == 201, created.json()
    thread = created.json()
    assert thread["participant"]["role"] == "PATIENT"
    assert thread["participant"]["name"] == "Sarah Jenkins"
    assert thread["patient_context"]["active_treatments"][0]["treatment_id"] == str(treatment.pk)
    stranger = PatientFactory()
    denied = doctor.post(
        "/api/v1/message-threads/", {"participant_id": str(stranger.pk)}, format="json"
    )
    assert denied.status_code == 403 and denied.json()["code"] == "not_in_care_team"

    patient_client = make_auth_client(patient)
    reply = patient_client.post(
        f"/api/v1/message-threads/{thread['id']}/messages/", {"body": "Much better"}, format="json"
    )
    assert reply.status_code == 201
    assert doctor.get("/api/v1/messages/unread-count/").json() == {"count": 1}
    listing = doctor.get("/api/v1/message-threads/?search=jenk").json()
    assert listing["count"] == 1 and listing["results"][0]["unread_count"] == 1
    messages = doctor.get(f"/api/v1/message-threads/{thread['id']}/messages/").json()["results"]
    assert messages[0]["sender"]["role"] == "PATIENT"
    doctor.post(
        f"/api/v1/message-threads/{thread['id']}/read/",
        {"up_to_message_id": messages[0]["id"]},
        format="json",
    )
    assert doctor.get("/api/v1/messages/unread-count/").json() == {"count": 0}
    sent = doctor.post(
        f"/api/v1/message-threads/{thread['id']}/messages/", {"body": "Great!"}, format="json"
    )
    assert sent.json()["sender"]["role"] == "PHYSICIAN"


def test_nurse_and_other_staff_messaging(
    patient: User, make_auth_client: Callable[[User], APIClient], staff_client: APIClient
) -> None:
    nurse = make_nurse()
    profile, _ = PatientProfile.objects.get_or_create(user=patient)
    profile.assigned_nurse = nurse
    profile.save()
    nurse_client = make_auth_client(nurse)
    created = nurse_client.post(
        "/api/v1/message-threads/", {"participant_id": str(patient.pk)}, format="json"
    )
    assert created.status_code == 201
    sent = nurse_client.post(
        f"/api/v1/message-threads/{created.json()['id']}/messages/", {"body": "Hi!"}, format="json"
    )
    assert sent.json()["sender"]["role"] == "NURSE"
    assert staff_client.get("/api/v1/message-threads/").status_code == 403
