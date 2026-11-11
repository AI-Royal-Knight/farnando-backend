"""Treatment read rules: schedule-derived next dose, downloads, renewal requests."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.html import strip_tags

from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, InvalidStateTransition
from apps.core.storage import presigned_url
from apps.treatments.models import (
    DoseFrequency,
    TimeOfDay,
    Treatment,
    TreatmentRenewalRequest,
    TreatmentStatus,
)

DOSE_HOURS: dict[str, int] = {
    TimeOfDay.MORNING: 8,
    TimeOfDay.AFTERNOON: 14,
    TimeOfDay.EVENING: 20,
    TimeOfDay.ANY: 9,
}


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def patient_timezone(user: Any) -> str:
    from apps.patients.services import get_or_create_profile

    return get_or_create_profile(user).timezone or settings.PORTAL_DEFAULT_TIMEZONE


def next_dose_due(
    treatment: Treatment, *, now: datetime | None = None, tz_name: str | None = None
) -> datetime | None:
    """Next scheduled dose in UTC. ``None`` for as-needed or inactive treatments."""

    if treatment.status != TreatmentStatus.ACTIVE:
        return None
    if treatment.frequency == DoseFrequency.AS_NEEDED:
        return None
    now = now or timezone.now()
    tz = ZoneInfo(tz_name or patient_timezone(treatment.patient))
    dose_time = time(DOSE_HOURS.get(treatment.time_of_day, 9))
    today = now.astimezone(tz).date()

    def at(day: date) -> datetime:
        return datetime.combine(day, dose_time, tzinfo=tz)

    start = treatment.started_at
    candidate: date
    if treatment.frequency == DoseFrequency.DAILY:
        candidate = max(today, start)
        if at(candidate) < now:
            candidate += timedelta(days=1)
    elif treatment.frequency == DoseFrequency.WEEKLY:
        weekday = treatment.day_of_week or start.isoweekday()
        candidate = max(today, start)
        while candidate.isoweekday() != weekday or at(candidate) < now:
            candidate += timedelta(days=1)
    elif treatment.frequency == DoseFrequency.EVERY_TWO_WEEKS:
        candidate = start
        if candidate < today:
            periods = (today - candidate).days // 14
            candidate += timedelta(days=14 * periods)
        while at(candidate) < now:
            candidate += timedelta(days=14)
    else:  # MONTHLY
        months = 0
        candidate = start
        while at(candidate) < now:
            months += 1
            candidate = _add_months(start, months)
    return at(candidate).astimezone(UTC)


def download_prescription(*, treatment: Treatment, request: Any) -> dict[str, Any]:
    key = treatment.prescription_pdf_key
    if not key and treatment.evaluation_id is not None:
        prescription = getattr(treatment.evaluation, "prescription", None)
        if prescription is not None and prescription.can_download:
            key = prescription.pdf_key
    if not key:
        raise InvalidStateTransition()
    ttl = settings.PRESCRIPTION_DOWNLOAD_TTL_SECONDS
    url = presigned_url(key, expires_in=ttl)
    record_audit(
        actor=request.user,
        action="prescription.downloaded",
        object_type="Treatment",
        object_id=treatment.pk,
        request=request,
    )
    return {"url": url, "expires_at": timezone.now() + timedelta(seconds=ttl)}


def prescription_pdf_available(treatment: Treatment) -> bool:
    if treatment.prescription_pdf_key:
        return True
    if treatment.evaluation_id is None:
        return False
    prescription = getattr(treatment.evaluation, "prescription", None)
    return bool(prescription is not None and prescription.pdf_key and prescription.can_download)


def request_renewal(*, treatment: Treatment, note: str, request: Any) -> TreatmentRenewalRequest:
    if treatment.status != TreatmentStatus.ACTIVE:
        raise InvalidStateTransition()
    if TreatmentRenewalRequest.objects.filter(
        treatment=treatment, resolved_at__isnull=True
    ).exists():
        raise Conflict("A renewal request is already pending.")
    try:
        with transaction.atomic():
            renewal = TreatmentRenewalRequest.objects.create(
                treatment=treatment, note=strip_tags(note or "").strip()
            )
    except IntegrityError as exc:
        raise Conflict("A renewal request is already pending.") from exc
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    notify(
        treatment.prescribing_physician,
        NotificationType.RENEWAL_REQUESTED,
        target_kind="renewal_request",
        target_id=renewal.pk,
        email=False,
    )
    record_audit(
        actor=request.user,
        action="treatment.renewal_requested",
        object_type="Treatment",
        object_id=treatment.pk,
        request=request,
    )
    return renewal


__all__ = [
    "DOSE_HOURS",
    "download_prescription",
    "next_dose_due",
    "patient_timezone",
    "prescription_pdf_available",
    "request_renewal",
]
