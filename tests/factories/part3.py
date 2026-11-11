"""Builders for Part 3 tests. Direct ORM setup keeps the tests fast and focused."""

from __future__ import annotations

import json
import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ConsentRecord, User
from apps.assessments.models import (
    Assessment,
    AssessmentMeasurement,
    Evaluation,
    EvaluationFee,
    Program,
)
from apps.consultations.models import (
    AvailabilityRule,
    CareRole,
    ClinicianProfile,
    Consultation,
    ConsultationType,
)
from apps.orders.models import Package
from apps.patients.models import Address, Ubigeo
from apps.payments import culqi, daily
from apps.treatments.models import Product, Treatment
from tests.factories.users import PhysicianFactory, StaffFactory

VERSION = "2026-11-01"
CONSENT_TYPES = ("TERMS_OF_SERVICE", "PRIVACY_POLICY", "HEALTH_DATA_PROCESSING")


def give_consents(user: User) -> None:
    for document_type in CONSENT_TYPES:
        ConsentRecord.objects.get_or_create(user=user, document_type=document_type, version=VERSION)


def program() -> Program:
    return Program.objects.get(slug="weight-loss")


def make_physician(*, name: str = "Marcus Vance", accepts: bool = True, **profile: Any) -> User:
    physician = PhysicianFactory(full_name=name)
    defaults = {
        "care_role": CareRole.PHYSICIAN,
        "display_name": f"Dr. {name}, MD",
        "headline": "Board-Certified Endocrinology & Metabolic Care",
        "specialty": "Endocrinology",
        "license_label": "CMP 12345",
        "accepts_new_consultations": accepts,
        "verification_status": "VERIFIED",
    }
    defaults.update(profile)
    ClinicianProfile.objects.create(user=physician, **defaults)
    for weekday in range(1, 8):
        AvailabilityRule.objects.create(
            physician=physician, weekday=weekday, start_time=time(8), end_time=time(20)
        )
    return physician


def make_nurse(name: str = "Sarah Torres") -> User:
    nurse = StaffFactory(full_name=name)
    ClinicianProfile.objects.create(
        user=nurse, care_role=CareRole.NURSE, display_name="Nurse Sarah", headline="Care team"
    )
    return nurse


def make_evaluation(
    user: User,
    status: str = "APPROVED",
    *,
    physician: User | None = None,
    labs_status: str = "SUBMITTED",
    assessment_status: str = "SUBMITTED",
    with_measurement: bool = False,
) -> tuple[Assessment, Evaluation | None]:
    prog = program()
    questionnaire = prog.published_questionnaire
    assert questionnaire is not None
    now = timezone.now()
    assessment = Assessment.objects.create(
        user=user,
        program=prog,
        questionnaire_version=questionnaire,
        status=assessment_status,
        submitted_at=now if assessment_status == "SUBMITTED" else None,
        screening_result="ELIGIBLE",
        labs_status=labs_status,
    )
    if with_measurement:
        AssessmentMeasurement.objects.create(
            assessment=assessment,
            current_weight_kg=Decimal("82"),
            target_weight_kg=Decimal("70"),
            height_cm=Decimal("175"),
            sex="FEMALE",
            date_of_birth=date(1990, 1, 1),
            bmi=Decimal("26.8"),
            bmi_category="OVERWEIGHT",
            target_bmi=Decimal("22.9"),
            weight_goal_kg=Decimal("12"),
        )
    if assessment_status != "SUBMITTED":
        return assessment, None
    evaluation = Evaluation.objects.create(
        assessment=assessment, status=status, physician=physician, submitted_at=now
    )
    EvaluationFee.objects.create(
        evaluation=evaluation,
        status="PAID" if status != "AWAITING_PAYMENT" else "PENDING",
        amount=Decimal("25.00"),
        currency="USD",
        title="Evaluation",
    )
    return assessment, evaluation


def product() -> Product:
    return Product.objects.get(code="semaglutide-0-5")


def make_treatment(
    user: User,
    physician: User,
    *,
    evaluation: Evaluation | None = None,
    refills: int | None = 2,
    **overrides: Any,
) -> Treatment:
    values: dict[str, Any] = {
        "patient": user,
        "program": program(),
        "evaluation": evaluation,
        "product": product(),
        "prescribing_physician": physician,
        "title": "Semaglutide 0.5mg",
        "medication_name": "Semaglutide GLP-1",
        "strength_label": "0.5 mg SubQ",
        "route": "SUBCUTANEOUS",
        "frequency": "WEEKLY",
        "day_of_week": 2,
        "time_of_day": "MORNING",
        "schedule_label": "Once weekly (SubQ)",
        "dose_value": Decimal("0.5"),
        "dose_unit": "mg",
        "started_at": timezone.localdate() - timedelta(days=30),
        "refills_total": refills,
        "refills_remaining": refills,
    }
    values.update(overrides)
    return Treatment.objects.create(**values)


def make_address(user: User, *, code: str = "150122", **overrides: Any) -> Address:
    values: dict[str, Any] = {
        "user": user,
        "label": "Home",
        "recipient_name": "Ana Quispe",
        "phone": "+51987654321",
        "line1": "Av. Larco 1234",
        "ubigeo": Ubigeo.objects.get(code=code),
        "is_default": not Address.objects.filter(user=user).exists(),
    }
    values.update(overrides)
    return Address.objects.create(**values)


def package(name: str = "Custom 3-Month Protocol") -> Package:
    return Package.objects.get(program=program(), name=name)


def consultation_type(code: str = "INITIAL") -> ConsultationType:
    return ConsultationType.objects.get(code=code)


def make_consultation(
    patient: User,
    physician: User,
    *,
    start_in: timedelta,
    status: str = "CONFIRMED",
    code: str = "INITIAL",
) -> Consultation:
    ctype = consultation_type(code)
    start = timezone.now() + start_in
    return Consultation.objects.create(
        patient=patient,
        physician=physician,
        consultation_type=ctype,
        status=status,
        start=start,
        end=start + timedelta(minutes=ctype.duration_minutes),
        confirmed_at=timezone.now() if status != "REQUESTED" else None,
    )


def idem() -> str:
    return str(uuid.uuid4())


def culqi_post(client: APIClient, payload: dict[str, Any], *, secret: str | None = None) -> Any:
    raw = json.dumps(payload).encode()
    return client.post(
        "/api/v1/webhooks/culqi/",
        data=raw,
        content_type="application/json",
        HTTP_X_CULQI_SIGNATURE=culqi.sign(raw, secret),
    )


def culqi_event(event_type: str, object_id: str, **data: Any) -> dict[str, Any]:
    body = {"id": object_id, "amount": 159900}
    body.update(data)
    return {"id": f"evt_{uuid.uuid4().hex}", "type": event_type, "data": body}


def daily_post(client: APIClient, payload: dict[str, Any]) -> Any:
    raw = json.dumps(payload).encode()
    timestamp = "1700000000"
    return client.post(
        "/api/v1/webhooks/video/daily/",
        data=raw,
        content_type="application/json",
        HTTP_X_WEBHOOK_TIMESTAMP=timestamp,
        HTTP_X_WEBHOOK_SIGNATURE=daily.sign(raw, timestamp),
    )
