"""Clinician consultations, availability editing, calendar and dashboard (Part 4, 3/6/7)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.assessments.enums import EvaluationStatus
from apps.assessments.models import Evaluation
from apps.clinical.evaluations import mine_for, patient_brief, queue_for
from apps.clinical.models import ClinicalNote, NoteKind
from apps.consultations import services as consultation_services
from apps.consultations.availability import (
    available_windows,
    blocked_intervals,
    busy_intervals,
    find_slot,
    is_covered,
    physician_timezone,
    subtract,
)
from apps.consultations.models import (
    OPEN_CONSULTATION_STATUSES,
    AvailabilityBlock,
    AvailabilityKind,
    AvailabilityRule,
    ClinicianProfile,
    Consultation,
    ConsultationStatus,
    ConsultationType,
    TimeBlockKind,
)
from apps.consultations.video import get_video_provider
from apps.core.audit import record_audit
from apps.core.exceptions import (
    ConflictsWithConsultations,
    InvalidStateTransition,
    JoinWindowClosed,
    OverlappingRule,
    SlotUnavailable,
)
from apps.messaging.services import clinician_unread_total
from apps.notifications.models import NotificationType
from apps.notifications.services import notify, unread_count
from apps.treatments.models import TreatmentRenewalRequest, TreatmentStatus

# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def consultation_item(consultation: Consultation) -> dict[str, Any]:
    note = consultation.patient_note or None
    agenda = consultation.agenda or None
    return {
        "id": consultation.pk,
        "status": consultation.status,
        "patient": patient_brief(consultation.patient),
        "mode": "TELEHEALTH",
        "consultation_type": {
            "id": consultation.consultation_type_id,
            "name": consultation.consultation_type.name,
        },
        "reason": note or agenda,
        "agenda": agenda,
        "start": consultation.start,
        "end": consultation.end,
        "timezone": consultation.timezone,
        "can_join": consultation_services.can_join(consultation),
        "can_accept": consultation.status == ConsultationStatus.REQUESTED
        and timezone.now() < consultation.start,
    }


def consultation_detail(consultation: Consultation) -> dict[str, Any]:
    body = consultation_item(consultation)
    evaluation = (
        Evaluation.objects.filter(assessment_id=consultation.assessment_id).first()
        if consultation.assessment_id
        else None
    )
    body.update(
        {
            "patient_note": consultation.patient_note or None,
            "join_opens_at": consultation_services.join_opens_at(consultation),
            "started_at": consultation.started_at,
            "ended_at": consultation.ended_at,
            "clinical_notes": (
                consultation.clinical_notes or None
                if consultation.status == ConsultationStatus.COMPLETED
                else None
            ),
            "visit_summary": consultation.visit_summary or None,
            "follow_up": consultation.follow_up,
            "evaluation_id": evaluation.pk if evaluation else None,
        }
    )
    return body


def own_consultations(physician: User) -> QuerySet[Consultation]:
    return Consultation.objects.select_related("consultation_type", "patient").filter(
        physician=physician
    )


def get_own(physician: User, consultation_id: Any, *, lock: bool = False) -> Consultation:
    queryset = own_consultations(physician)
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    consultation = queryset.filter(pk=consultation_id).first()
    if consultation is None:
        raise NotFound()
    return consultation


def week_bounds(physician: User) -> tuple[datetime, datetime]:
    tz = ZoneInfo(physician_timezone(physician))
    today = timezone.now().astimezone(tz).date()
    monday = today - timedelta(days=today.isoweekday() - 1)
    start = datetime.combine(monday, time.min, tzinfo=tz)
    return start, start + timedelta(days=7)


def this_week_count(physician: User) -> int:
    start, end = week_bounds(physician)
    return (
        own_consultations(physician)
        .filter(start__gte=start, start__lt=end)
        .exclude(status=ConsultationStatus.CANCELLED)
        .count()
    )


def today_consultations(physician: User) -> list[Consultation]:
    tz = ZoneInfo(physician_timezone(physician))
    today = timezone.now().astimezone(tz).date()
    start = datetime.combine(today, time.min, tzinfo=tz)
    return list(
        own_consultations(physician)
        .filter(start__gte=start, start__lt=start + timedelta(days=1))
        .exclude(
            status__in=(
                ConsultationStatus.CANCELLED,
                ConsultationStatus.DECLINED,
                ConsultationStatus.EXPIRED,
            )
        )
        .order_by("start")
    )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _audit(physician: User, consultation: Consultation, action: str, request: Any) -> None:
    record_audit(
        actor=physician,
        action=f"consultation.{action}",
        object_type="Consultation",
        object_id=consultation.pk,
        request=request,
    )


def accept(*, physician: User, consultation_id: Any, request: Any) -> Consultation:
    with transaction.atomic():
        consultation = get_own(physician, consultation_id, lock=True)
        if (
            consultation.status != ConsultationStatus.REQUESTED
            or timezone.now() >= consultation.start
        ):
            raise InvalidStateTransition()
        consultation_services.transition(
            consultation, ConsultationStatus.CONFIRMED, actor=physician, request=request
        )
    return consultation


def decline(
    *, physician: User, consultation_id: Any, reason: str, patient_note: str, request: Any
) -> Consultation:
    with transaction.atomic():
        consultation = get_own(physician, consultation_id, lock=True)
        if consultation.status != ConsultationStatus.REQUESTED:
            raise InvalidStateTransition()
        text = strip_tags(reason).strip()
        note = strip_tags(patient_note or "").strip()
        consultation_services.transition(
            consultation,
            ConsultationStatus.DECLINED,
            actor=physician,
            reason=f"{text}\nPatient note: {note}" if note else text,
            request=request,
        )
    return consultation


def reschedule(
    *, physician: User, consultation_id: Any, start: datetime, reason: str, request: Any
) -> Consultation:
    try:
        with transaction.atomic():
            ClinicianProfile.objects.select_for_update().filter(user=physician).first()
            consultation = get_own(physician, consultation_id, lock=True)
            if consultation.status not in (
                ConsultationStatus.REQUESTED,
                ConsultationStatus.CONFIRMED,
            ):
                raise InvalidStateTransition()
            minimum = timedelta(hours=settings.PHYSICIAN_RESCHEDULE_MIN_HOURS)
            if timezone.now() > consultation.start - minimum:
                from apps.core.exceptions import TooLateToChange

                raise TooLateToChange()
            slot = find_slot(
                physician,
                consultation.consultation_type,
                start,
                exclude_consultation_id=consultation.pk,
                apply_lead_time=False,
            )
            if slot is None or slot.start <= timezone.now():
                raise SlotUnavailable()
            consultation.start = slot.start
            consultation.end = slot.end
            consultation.rescheduled_by_physician_at = timezone.now()
            consultation.reminder_24h_sent_at = None
            consultation.reminder_1h_sent_at = None
            consultation.save(
                update_fields=[
                    "start",
                    "end",
                    "rescheduled_by_physician_at",
                    "reminder_24h_sent_at",
                    "reminder_1h_sent_at",
                    "updated_at",
                ]
            )
            from apps.consultations.models import ConsultationStatusHistory

            ConsultationStatusHistory.objects.create(
                consultation=consultation,
                from_status=consultation.status,
                to_status=consultation.status,
                actor=physician,
                reason=strip_tags(reason).strip(),
            )
            notify(
                consultation.patient,
                NotificationType.CONSULTATION_REMINDER,
                target_kind="consultation",
                target_id=consultation.pk,
            )
            _audit(physician, consultation, "rescheduled_by_physician", request)
    except IntegrityError as exc:
        raise SlotUnavailable() from exc
    return consultation


def cancel(*, physician: User, consultation_id: Any, reason: str, request: Any) -> Consultation:
    with transaction.atomic():
        consultation = get_own(physician, consultation_id, lock=True)
        if consultation.status not in (ConsultationStatus.REQUESTED, ConsultationStatus.CONFIRMED):
            raise InvalidStateTransition()
        consultation_services.transition(
            consultation,
            ConsultationStatus.CANCELLED,
            actor=physician,
            reason=strip_tags(reason).strip(),
            request=request,
        )
        consultation.cancelled_by = physician
        consultation.save(update_fields=["cancelled_by", "updated_at"])
    return consultation


def join(*, physician: User, consultation_id: Any, request: Any) -> dict[str, Any]:
    consultation = get_own(physician, consultation_id)
    if not consultation_services.can_join(consultation):
        raise JoinWindowClosed(
            details={"join_opens_at": consultation_services.join_opens_at(consultation)}
        )
    provider = get_video_provider()
    expires_at = consultation_services.join_closes_at(consultation)
    with transaction.atomic():
        locked = get_own(physician, consultation_id, lock=True)
        if not locked.video_room_name:
            locked.video_room_name = f"consultation-{locked.pk.hex}"
            locked.video_room_url = provider.ensure_room(
                room_name=locked.video_room_name,
                expires_at=expires_at,
                recording=bool(settings.VIDEO_RECORDING_ENABLED),
            )
        if locked.physician_joined_at is None:
            locked.physician_joined_at = timezone.now()
        locked.save(
            update_fields=[
                "video_room_name",
                "video_room_url",
                "physician_joined_at",
                "updated_at",
            ]
        )
        if locked.status == ConsultationStatus.CONFIRMED:
            consultation_services.transition(
                locked,
                ConsultationStatus.IN_PROGRESS,
                actor=physician,
                reason="Physician started the call",
                request=request,
            )
    token = provider.create_token(
        room_name=locked.video_room_name,
        user_id=str(physician.pk),
        display_name=physician.get_full_name(),
        expires_at=expires_at,
        is_owner=True,
    )
    _audit(physician, locked, "joined_by_physician", request)
    return {
        "provider": provider.name,
        "room_url": locked.video_room_url,
        "token": token,
        "display_name": physician.get_full_name(),
        "expires_at": expires_at,
        "consultation": locked,
    }


def update_agenda(*, physician: User, consultation_id: Any, agenda: str) -> Consultation:
    consultation = get_own(physician, consultation_id)
    if consultation.status not in OPEN_CONSULTATION_STATUSES:
        raise InvalidStateTransition()
    consultation.agenda = strip_tags(agenda or "").strip()
    consultation.save(update_fields=["agenda", "updated_at"])
    return consultation


def complete(
    *, physician: User, consultation_id: Any, data: dict[str, Any], request: Any
) -> Consultation:
    with transaction.atomic():
        consultation = get_own(physician, consultation_id, lock=True)
        if consultation.status == ConsultationStatus.CONFIRMED:
            if consultation.physician_joined_at is None:
                raise InvalidStateTransition()
            consultation_services.transition(
                consultation, ConsultationStatus.IN_PROGRESS, actor=physician, request=request
            )
        if consultation.status != ConsultationStatus.IN_PROGRESS:
            raise InvalidStateTransition()
        follow_up = data.get("follow_up")
        if follow_up:
            if not ConsultationType.objects.filter(
                pk=follow_up["consultation_type_id"], is_active=True
            ).exists():
                raise ValidationError({"follow_up.consultation_type_id": ["Unknown type."]})
            follow_up = {
                "consultation_type_id": str(follow_up["consultation_type_id"]),
                "suggested_in_days": follow_up["suggested_in_days"],
            }
        notes = strip_tags(data["clinical_notes"]).strip()
        consultation.clinical_notes = notes
        consultation.visit_summary = strip_tags(data.get("visit_summary") or "").strip()
        consultation.follow_up = follow_up or None
        consultation.save(
            update_fields=["clinical_notes", "visit_summary", "follow_up", "updated_at"]
        )
        consultation_services.transition(
            consultation, ConsultationStatus.COMPLETED, actor=physician, request=request
        )
        ClinicalNote.objects.create(
            patient=consultation.patient,
            author=physician,
            kind=NoteKind.CLINICAL,
            body=notes,
            consultation=consultation,
        )
    if consultation.video_room_name:
        get_video_provider().end_room(room_name=consultation.video_room_name)
    return consultation


def mark_no_show(*, physician: User, consultation_id: Any, note: str, request: Any) -> Consultation:
    with transaction.atomic():
        consultation = get_own(physician, consultation_id, lock=True)
        grace = timedelta(minutes=settings.CONSULTATION_JOIN_GRACE_MINUTES)
        if consultation.status != ConsultationStatus.CONFIRMED or timezone.now() < (
            consultation.start + grace
        ):
            raise InvalidStateTransition()
        consultation_services.transition(
            consultation,
            ConsultationStatus.NO_SHOW,
            actor=physician,
            reason=strip_tags(note or "").strip() or "Marked by physician",
            request=request,
        )
    return consultation


# ---------------------------------------------------------------------------
# Availability rules and time blocks
# ---------------------------------------------------------------------------


def _future_open(physician: User) -> list[Consultation]:
    return list(
        Consultation.objects.filter(
            physician=physician,
            status__in=OPEN_CONSULTATION_STATUSES,
            end__gt=timezone.now(),
        )
    )


def _covered_ids(physician: User, consultations: list[Consultation]) -> set[Any]:
    rules = list(
        AvailabilityRule.objects.filter(physician=physician).prefetch_related("consultation_types")
    )
    blocks = list(AvailabilityBlock.objects.filter(physician=physician))
    return {
        item.pk
        for item in consultations
        if is_covered(physician, item.start, item.end, rules=rules, blocks=blocks)
    }


def guarded_change(physician: User, change: Any) -> Any:
    """Run ``change`` and roll back if it uncovers a booked consultation."""

    consultations = _future_open(physician)
    before = _covered_ids(physician, consultations)
    with transaction.atomic():
        result = change()
        lost = before - _covered_ids(physician, consultations)
        if lost:
            raise ConflictsWithConsultations(
                details={"consultation_ids": sorted(str(item) for item in lost)}
            )
    return result


def _validate_rule(physician: User, data: dict[str, Any], exclude_id: Any = None) -> None:
    start, end = data["start_time"], data["end_time"]
    for value, field in ((start, "start_time"), (end, "end_time")):
        if value.minute % 15 or value.second:
            raise ValidationError({field: ["Use 15-minute steps."]})
    if end <= start:
        raise ValidationError({"end_time": ["Must be after start_time."]})
    valid_from, valid_until = data.get("valid_from"), data.get("valid_until")
    if valid_from and valid_until and valid_until < valid_from:
        raise ValidationError({"valid_until": ["Must be on or after valid_from."]})
    if data["kind"] != AvailabilityKind.AVAILABLE:
        return
    others = AvailabilityRule.objects.filter(
        physician=physician, kind=AvailabilityKind.AVAILABLE, weekday=data["day_of_week"]
    ).exclude(pk=exclude_id)
    for other in others:
        overlaps_time = start < other.end_time and other.start_time < end
        overlaps_dates = (
            valid_until is None or other.valid_from is None or other.valid_from <= valid_until
        ) and (valid_from is None or other.valid_until is None or valid_from <= other.valid_until)
        if overlaps_time and overlaps_dates:
            raise OverlappingRule()


def save_rule(
    *, physician: User, data: dict[str, Any], rule: AvailabilityRule | None = None
) -> AvailabilityRule:
    merged = rule_data(rule) if rule is not None else {}
    merged.update(data)
    merged.setdefault("valid_from", None)
    merged.setdefault("valid_until", None)
    _validate_rule(physician, merged, exclude_id=rule.pk if rule else None)
    type_ids = merged.get("consultation_type_ids")
    types: list[ConsultationType] = []
    if type_ids:
        types = list(ConsultationType.objects.filter(pk__in=type_ids))
        if len(types) != len(set(type_ids)):
            raise ValidationError({"consultation_type_ids": ["Unknown consultation type."]})

    def change() -> AvailabilityRule:
        target = rule or AvailabilityRule(physician=physician)
        target.kind = merged["kind"]
        target.weekday = merged["day_of_week"]
        target.start_time = merged["start_time"]
        target.end_time = merged["end_time"]
        target.valid_from = merged.get("valid_from")
        target.valid_until = merged.get("valid_until")
        target.label = (merged.get("label") or "").strip()
        target.save()
        target.consultation_types.set(types if target.kind == AvailabilityKind.AVAILABLE else [])
        return target

    result: AvailabilityRule = guarded_change(physician, change)
    return result


def delete_rule(*, physician: User, rule: AvailabilityRule) -> None:
    guarded_change(physician, rule.delete)


def rule_data(rule: AvailabilityRule) -> dict[str, Any]:
    type_ids = [item.pk for item in rule.consultation_types.all()]
    return {
        "id": rule.pk,
        "kind": rule.kind,
        "day_of_week": rule.weekday,
        "start_time": rule.start_time,
        "end_time": rule.end_time,
        "consultation_type_ids": type_ids or None,
        "valid_from": rule.valid_from,
        "valid_until": rule.valid_until,
        "label": rule.label or None,
    }


def save_block(
    *, physician: User, data: dict[str, Any], block: AvailabilityBlock | None = None
) -> AvailabilityBlock:
    merged = block_data(block) if block is not None else {}
    merged.update(data)
    start, end = merged["start"], merged["end"]
    if start <= timezone.now():
        raise ValidationError({"start": ["Must be in the future."]})
    if end <= start:
        raise ValidationError({"end": ["Must be after start."]})
    if merged["kind"] == TimeBlockKind.EXTRA_AVAILABLE and end - start > timedelta(hours=24):
        raise ValidationError({"end": ["Extra availability is at most 24 hours long."]})

    def change() -> AvailabilityBlock:
        target = block or AvailabilityBlock(physician=physician)
        target.kind = merged["kind"]
        target.start = start
        target.end = end
        target.reason = (merged.get("label") or "").strip()
        target.save()
        return target

    result: AvailabilityBlock = guarded_change(physician, change)
    return result


def delete_block(*, physician: User, block: AvailabilityBlock) -> None:
    guarded_change(physician, block.delete)


def block_data(block: AvailabilityBlock) -> dict[str, Any]:
    return {
        "id": block.pk,
        "kind": block.kind,
        "start": block.start,
        "end": block.end,
        "label": block.reason or None,
    }


# ---------------------------------------------------------------------------
# Calendar and dashboard
# ---------------------------------------------------------------------------


def calendar(physician: User, date_from: date, date_to: date) -> dict[str, Any]:
    blocked = blocked_intervals(physician, date_from, date_to)
    busy = busy_intervals(physician, date_from, date_to)
    free = subtract(
        available_windows(physician, date_from, date_to),
        [(item.start, item.end) for item in blocked] + busy,
    )
    tz = ZoneInfo(physician_timezone(physician))
    start = datetime.combine(date_from, time.min, tzinfo=tz)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
    consultations = (
        own_consultations(physician)
        .filter(status__in=OPEN_CONSULTATION_STATUSES, start__lt=end, end__gt=start)
        .order_by("start")
    )
    items: list[dict[str, Any]] = [
        {"type": "AVAILABLE_SLOT", "start": s, "end": e, "consultation": None, "block": None}
        for s, e in free
    ]
    items += [
        {
            "type": "CONSULTATION",
            "start": item.start,
            "end": item.end,
            "consultation": consultation_item(item),
            "block": None,
        }
        for item in consultations
    ]
    items += [
        {
            "type": "BLOCK",
            "start": item.start,
            "end": item.end,
            "consultation": None,
            "block": {"id": item.id, "label": item.label or None, "source": item.source},
        }
        for item in blocked
    ]
    items.sort(key=lambda item: item["start"])
    return {"timezone": str(tz), "items": items}


def dashboard(physician: User) -> dict[str, Any]:
    from apps.clinical.permissions import physician_profile
    from apps.clinical.treatments import care_patients

    profile = physician_profile(physician)
    awaiting = (
        queue_for(physician, profile).count()
        + mine_for(physician)
        .filter(status__in=(EvaluationStatus.IN_REVIEW, EvaluationStatus.PENDING_LABS))
        .count()
    )
    in_treatment = (
        care_patients(physician)
        .filter(
            treatments__prescribing_physician=physician,
            treatments__status=TreatmentStatus.ACTIVE,
        )
        .distinct()
        .count()
    )
    renewals = TreatmentRenewalRequest.objects.filter(
        treatment__prescribing_physician=physician, status="REQUESTED"
    ).count()
    return {
        "awaiting_evaluation_count": awaiting,
        "in_treatment_count": in_treatment,
        "consultations_this_week_count": this_week_count(physician),
        "pending_renewal_requests_count": renewals,
        "unread_messages_count": clinician_unread_total(physician),
        "unread_notifications_count": unread_count(physician),
        "today_consultations": [consultation_item(item) for item in today_consultations(physician)],
    }


__all__ = [
    "accept",
    "block_data",
    "calendar",
    "cancel",
    "complete",
    "consultation_detail",
    "consultation_item",
    "dashboard",
    "decline",
    "delete_block",
    "delete_rule",
    "get_own",
    "join",
    "mark_no_show",
    "own_consultations",
    "reschedule",
    "rule_data",
    "save_block",
    "save_rule",
    "this_week_count",
    "update_agenda",
]
