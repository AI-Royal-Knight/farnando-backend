"""Booking rules and the consultation state machine (contract Part 3, 4.11)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.accounts.services import ConsentService
from apps.assessments.enums import EvaluationStatus
from apps.assessments.models import Assessment, Evaluation
from apps.catalog import runtime
from apps.consultations.availability import find_slot, physician_timezone
from apps.consultations.clinicians import profile_for
from apps.consultations.models import (
    CareRole,
    ClinicianProfile,
    Consultation,
    ConsultationStatus,
    ConsultationStatusHistory,
    ConsultationType,
)
from apps.consultations.video import get_video_provider
from apps.core.audit import record_audit
from apps.core.exceptions import (
    Conflict,
    ConsentRequired,
    EvaluationRequired,
    InvalidStateTransition,
    JoinWindowClosed,
    SlotUnavailable,
    TooLateToChange,
)
from apps.notifications.models import NotificationType
from apps.notifications.services import notify

logger = structlog.get_logger(__name__)

ELIGIBLE_EVALUATION_STATUSES = (
    EvaluationStatus.SUBMITTED,
    EvaluationStatus.IN_REVIEW,
    EvaluationStatus.PENDING_LABS,
    EvaluationStatus.APPROVED,
)

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ConsultationStatus.REQUESTED: {
        ConsultationStatus.CONFIRMED,
        ConsultationStatus.DECLINED,
        ConsultationStatus.EXPIRED,
        ConsultationStatus.REQUESTED,
        ConsultationStatus.CANCELLED,
    },
    ConsultationStatus.CONFIRMED: {
        ConsultationStatus.REQUESTED,
        ConsultationStatus.CANCELLED,
        ConsultationStatus.IN_PROGRESS,
        ConsultationStatus.NO_SHOW,
    },
    ConsultationStatus.IN_PROGRESS: {ConsultationStatus.COMPLETED},
    ConsultationStatus.COMPLETED: set(),
    ConsultationStatus.CANCELLED: set(),
    ConsultationStatus.DECLINED: set(),
    ConsultationStatus.EXPIRED: set(),
    ConsultationStatus.NO_SHOW: set(),
}

PATIENT_NOTIFICATIONS: dict[str, str] = {
    ConsultationStatus.CONFIRMED: NotificationType.CONSULTATION_CONFIRMED,
    ConsultationStatus.DECLINED: NotificationType.CONSULTATION_DECLINED,
    ConsultationStatus.CANCELLED: NotificationType.CONSULTATION_CANCELLED,
}


def _notify_physician(consultation: Consultation, notification_type: str) -> None:
    notify(
        consultation.physician,
        notification_type,
        target_kind="clinical_consultation",
        target_id=consultation.pk,
        email=False,
    )


def has_consultation_eligibility(user: User) -> bool:
    return Evaluation.objects.filter(
        assessment__user=user, status__in=ELIGIBLE_EVALUATION_STATUSES
    ).exists()


def join_opens_at(consultation: Consultation) -> datetime:
    return consultation.start - timedelta(
        minutes=runtime.hours("booking", "join_window_before_minutes")
    )


def join_closes_at(consultation: Consultation) -> datetime:
    return consultation.end + timedelta(
        minutes=runtime.hours("booking", "join_window_after_minutes")
    )


def can_join(consultation: Consultation, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    return consultation.status in (
        ConsultationStatus.CONFIRMED,
        ConsultationStatus.IN_PROGRESS,
    ) and join_opens_at(consultation) <= now <= join_closes_at(consultation)


def _changeable(consultation: Consultation, hours: int, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    return consultation.status in (
        ConsultationStatus.REQUESTED,
        ConsultationStatus.CONFIRMED,
    ) and now <= consultation.start - timedelta(hours=hours)


def can_reschedule(consultation: Consultation, now: datetime | None = None) -> bool:
    return _changeable(consultation, runtime.hours("booking", "reschedule_min_hours_before"), now)


def can_cancel(consultation: Consultation, now: datetime | None = None) -> bool:
    if consultation.rescheduled_by_physician_at is not None:
        now = now or timezone.now()
        return (
            consultation.status in (ConsultationStatus.REQUESTED, ConsultationStatus.CONFIRMED)
            and now < consultation.start
        )
    return _changeable(consultation, runtime.hours("booking", "cancel_min_hours_before"), now)


def transition(
    consultation: Consultation,
    to_status: str,
    *,
    actor: Any,
    reason: str = "",
    request: Any = None,
) -> Consultation:
    from_status = consultation.status
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise InvalidStateTransition()
    now = timezone.now()
    consultation.status = to_status
    fields = ["status", "updated_at"]
    if to_status == ConsultationStatus.CONFIRMED:
        consultation.confirmed_at = now
        fields.append("confirmed_at")
    elif to_status == ConsultationStatus.REQUESTED:
        consultation.confirmed_at = None
        fields.append("confirmed_at")
    elif to_status == ConsultationStatus.IN_PROGRESS and consultation.started_at is None:
        consultation.started_at = now
        fields.append("started_at")
    elif to_status == ConsultationStatus.COMPLETED and consultation.ended_at is None:
        consultation.ended_at = now
        fields.append("ended_at")
    elif to_status == ConsultationStatus.CANCELLED:
        consultation.cancelled_at = now
        consultation.cancellation_reason = reason
        fields.extend(["cancelled_at", "cancellation_reason"])
    consultation.save(update_fields=fields)
    ConsultationStatusHistory.objects.create(
        consultation=consultation,
        from_status=from_status,
        to_status=to_status,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        reason=reason,
    )
    record_audit(
        actor=actor,
        action="consultation.status_changed",
        object_type="Consultation",
        object_id=consultation.pk,
        request=request,
        metadata={"from": from_status, "to": to_status},
    )
    notification_type = PATIENT_NOTIFICATIONS.get(to_status)
    is_patient_actor = getattr(actor, "pk", None) == consultation.patient_id
    if notification_type is not None and not is_patient_actor:
        notify(
            consultation.patient,
            notification_type,
            target_kind="consultation",
            target_id=consultation.pk,
        )
    return consultation


def _lock_physician(physician: User) -> None:
    ClinicianProfile.objects.select_for_update().filter(user=physician).first()


def _validate_physician(physician_id: Any) -> User:
    physician = User.objects.filter(pk=physician_id, role=Role.PHYSICIAN, is_active=True).first()
    profile = profile_for(physician) if physician is not None else None
    if (
        physician is None
        or profile is None
        or profile.care_role != CareRole.PHYSICIAN
        or profile.verification_status != "VERIFIED"
        or not profile.accepts_new_consultations
    ):
        raise ValidationError({"physician_id": ["Select an available physician."]})
    return cast(User, physician)


def book(*, patient: User, data: dict[str, Any], request: Any) -> Consultation:
    if not has_consultation_eligibility(patient):
        raise EvaluationRequired()
    if not ConsentService.has_current_consents(patient):
        raise ConsentRequired()
    physician = _validate_physician(data["physician_id"])
    consultation_type = ConsultationType.objects.filter(
        pk=data["consultation_type_id"], is_active=True
    ).first()
    if consultation_type is None:
        raise ValidationError({"consultation_type_id": ["Unknown consultation type."]})
    assessment = None
    if data.get("assessment_id"):
        assessment = Assessment.objects.filter(pk=data["assessment_id"], user=patient).first()
        if assessment is None:
            raise ValidationError({"assessment_id": ["Unknown assessment."]})
    note = strip_tags(data.get("patient_note") or "").strip()
    start: datetime = data["start"]

    try:
        with transaction.atomic():
            _lock_physician(physician)
            if Consultation.objects.filter(
                patient=patient,
                consultation_type=consultation_type,
                status__in=(ConsultationStatus.REQUESTED, ConsultationStatus.CONFIRMED),
            ).exists():
                raise Conflict("You already have an open consultation of this type.")
            slot = find_slot(physician, consultation_type, start)
            if slot is None:
                raise SlotUnavailable()
            consultation = Consultation.objects.create(
                patient=patient,
                physician=physician,
                consultation_type=consultation_type,
                assessment=assessment,
                status=ConsultationStatus.REQUESTED,
                start=slot.start,
                end=slot.end,
                timezone=physician_timezone(physician),
                patient_note=note,
            )
            ConsultationStatusHistory.objects.create(
                consultation=consultation,
                from_status="",
                to_status=ConsultationStatus.REQUESTED,
                actor=patient,
            )
            record_audit(
                actor=patient,
                action="consultation.requested",
                object_type="Consultation",
                object_id=consultation.pk,
                request=request,
            )
            _notify_physician(consultation, NotificationType.CONSULTATION_REQUESTED)
    except IntegrityError as exc:
        raise SlotUnavailable() from exc
    return consultation


def reschedule(*, consultation: Consultation, start: datetime, request: Any) -> Consultation:
    if consultation.status not in (ConsultationStatus.REQUESTED, ConsultationStatus.CONFIRMED):
        raise InvalidStateTransition()
    if not can_reschedule(consultation):
        raise TooLateToChange()
    try:
        with transaction.atomic():
            _lock_physician(consultation.physician)
            locked = Consultation.objects.select_for_update().get(pk=consultation.pk)
            slot = find_slot(
                locked.physician,
                locked.consultation_type,
                start,
                exclude_consultation_id=locked.pk,
            )
            if slot is None:
                raise SlotUnavailable()
            locked.start = slot.start
            locked.end = slot.end
            locked.reminder_24h_sent_at = None
            locked.reminder_1h_sent_at = None
            locked.save(
                update_fields=[
                    "start",
                    "end",
                    "reminder_24h_sent_at",
                    "reminder_1h_sent_at",
                    "updated_at",
                ]
            )
            transition(
                locked,
                ConsultationStatus.REQUESTED,
                actor=locked.patient,
                reason="Rescheduled by patient",
                request=request,
            )
            _notify_physician(locked, NotificationType.CONSULTATION_RESCHEDULED_BY_PATIENT)
    except IntegrityError as exc:
        raise SlotUnavailable() from exc
    return locked


def cancel(*, consultation: Consultation, reason: str, request: Any) -> Consultation:
    if consultation.status not in (ConsultationStatus.REQUESTED, ConsultationStatus.CONFIRMED):
        raise InvalidStateTransition()
    if not can_cancel(consultation):
        raise TooLateToChange()
    with transaction.atomic():
        locked = Consultation.objects.select_for_update().get(pk=consultation.pk)
        transition(
            locked,
            ConsultationStatus.CANCELLED,
            actor=locked.patient,
            reason=strip_tags(reason or "").strip(),
            request=request,
        )
        locked.cancelled_by = locked.patient
        locked.save(update_fields=["cancelled_by", "updated_at"])
        _notify_physician(locked, NotificationType.CONSULTATION_CANCELLED_BY_PATIENT)
    return locked


def join(*, consultation: Consultation, user: User, request: Any) -> dict[str, Any]:
    if consultation.status not in (
        ConsultationStatus.CONFIRMED,
        ConsultationStatus.IN_PROGRESS,
    ) or not can_join(consultation):
        raise JoinWindowClosed(details={"join_opens_at": join_opens_at(consultation)})
    provider = get_video_provider()
    expires_at = join_closes_at(consultation)
    with transaction.atomic():
        locked = Consultation.objects.select_for_update().get(pk=consultation.pk)
        if not locked.video_room_name:
            locked.video_room_name = f"consultation-{locked.pk.hex}"
            locked.video_room_url = provider.ensure_room(
                room_name=locked.video_room_name,
                expires_at=expires_at,
                recording=bool(settings.VIDEO_RECORDING_ENABLED),
            )
            locked.save(update_fields=["video_room_name", "video_room_url", "updated_at"])
    display_name = user.get_full_name()
    token = provider.create_token(
        room_name=locked.video_room_name,
        user_id=str(user.pk),
        display_name=display_name,
        expires_at=expires_at,
    )
    record_audit(
        actor=user,
        action="consultation.joined",
        object_type="Consultation",
        object_id=locked.pk,
        request=request,
    )
    return {
        "provider": provider.name,
        "room_url": locked.video_room_url,
        "token": token,
        "display_name": display_name,
        "expires_at": expires_at,
        "consultation": locked,
    }


def leave(*, consultation: Consultation, request: Any) -> None:
    if consultation.status not in (ConsultationStatus.CONFIRMED, ConsultationStatus.IN_PROGRESS):
        raise InvalidStateTransition()
    consultation.patient_left_at = timezone.now()
    consultation.save(update_fields=["patient_left_at", "updated_at"])
    record_audit(
        actor=consultation.patient,
        action="consultation.left",
        object_type="Consultation",
        object_id=consultation.pk,
        request=request,
    )


# --- Part 4 entry points (physician side), used by tests and future views ---


def physician_accept(consultation: Consultation, *, actor: User) -> Consultation:
    with transaction.atomic():
        return transition(consultation, ConsultationStatus.CONFIRMED, actor=actor)


def physician_decline(consultation: Consultation, *, actor: User, reason: str = "") -> Consultation:
    with transaction.atomic():
        return transition(consultation, ConsultationStatus.DECLINED, actor=actor, reason=reason)


def physician_complete(consultation: Consultation, *, actor: User) -> Consultation:
    with transaction.atomic():
        return transition(consultation, ConsultationStatus.COMPLETED, actor=actor)


# --- Video webhook side effects ---


def participant_joined(consultation: Consultation) -> None:
    with transaction.atomic():
        locked = Consultation.objects.select_for_update().get(pk=consultation.pk)
        if locked.status == ConsultationStatus.CONFIRMED:
            transition(locked, ConsultationStatus.IN_PROGRESS, actor=None, reason="First join")
        elif locked.status == ConsultationStatus.IN_PROGRESS and locked.started_at is None:
            locked.started_at = timezone.now()
            locked.save(update_fields=["started_at", "updated_at"])


def meeting_ended(consultation: Consultation) -> None:
    if consultation.ended_at is None and consultation.status in (
        ConsultationStatus.IN_PROGRESS,
        ConsultationStatus.COMPLETED,
    ):
        consultation.ended_at = timezone.now()
        consultation.save(update_fields=["ended_at", "updated_at"])


# --- Scheduled jobs ---


def expire_requests(now: datetime | None = None) -> int:
    now = now or timezone.now()
    cutoff = now + timedelta(hours=settings.CONSULTATION_REQUEST_EXPIRY_HOURS)
    count = 0
    for consultation in Consultation.objects.filter(
        status=ConsultationStatus.REQUESTED, start__lte=cutoff
    ):
        with transaction.atomic():
            transition(consultation, ConsultationStatus.EXPIRED, actor=None, reason="Not accepted")
        count += 1
    return count


def mark_no_shows(now: datetime | None = None) -> int:
    now = now or timezone.now()
    grace = timedelta(minutes=runtime.hours("booking", "join_window_after_minutes"))
    count = 0
    for consultation in Consultation.objects.filter(
        status=ConsultationStatus.CONFIRMED, started_at__isnull=True, end__lte=now - grace
    ):
        with transaction.atomic():
            transition(consultation, ConsultationStatus.NO_SHOW, actor=None, reason="Nobody joined")
        count += 1
    return count


def send_reminders(now: datetime | None = None) -> int:
    """24 h and 1 h reminders. One run never sends two reminders for one visit."""

    now = now or timezone.now()
    long_hours = max(settings.CONSULTATION_REMINDER_HOURS)
    short_hours = min(settings.CONSULTATION_REMINDER_HOURS)
    sent = 0
    queryset = Consultation.objects.filter(
        status=ConsultationStatus.CONFIRMED,
        start__gt=now,
        start__lte=now + timedelta(hours=long_hours),
    ).select_related("patient")
    for consultation in queryset:
        fields: list[str] = []
        due = False
        if consultation.start <= now + timedelta(hours=short_hours):
            if consultation.reminder_1h_sent_at is None:
                consultation.reminder_1h_sent_at = now
                fields.append("reminder_1h_sent_at")
                due = True
            if consultation.reminder_24h_sent_at is None:
                consultation.reminder_24h_sent_at = now
                fields.append("reminder_24h_sent_at")
        elif consultation.reminder_24h_sent_at is None:
            consultation.reminder_24h_sent_at = now
            fields.append("reminder_24h_sent_at")
            due = True
        if not fields:
            continue
        consultation.save(update_fields=[*fields, "updated_at"])
        if due:
            notify(
                consultation.patient,
                NotificationType.CONSULTATION_REMINDER,
                target_kind="consultation",
                target_id=consultation.pk,
            )
            sent += 1
    return sent


__all__ = [
    "ELIGIBLE_EVALUATION_STATUSES",
    "book",
    "can_cancel",
    "can_join",
    "can_reschedule",
    "cancel",
    "expire_requests",
    "has_consultation_eligibility",
    "join",
    "join_closes_at",
    "join_opens_at",
    "leave",
    "mark_no_shows",
    "meeting_ended",
    "participant_joined",
    "physician_accept",
    "physician_complete",
    "physician_decline",
    "reschedule",
    "send_reminders",
    "transition",
]
