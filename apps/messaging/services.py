"""Thread and message rules. Notifications never include the message text."""

from __future__ import annotations

import hashlib
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.consultations.clinicians import care_role_for, physician_summary
from apps.core.exceptions import NotInCareTeam
from apps.messaging.models import Message, MessageSenderRole, MessageThread
from apps.notifications.models import NotificationType
from apps.notifications.services import notify
from apps.patients.services import is_in_care_team

MAX_BODY = 4000
PREVIEW_CHARS = 120


def clean_body(raw: str) -> str:
    body = strip_tags(raw or "").strip()
    if not body:
        raise ValidationError({"body": ["Message cannot be empty."]})
    if len(body) > MAX_BODY:
        raise ValidationError(
            {"body": [f"Ensure this field has no more than {MAX_BODY} characters."]}
        )
    return body


def sender_role_for(thread: MessageThread, user: User) -> str:
    if user.pk == thread.patient_id:
        return MessageSenderRole.PATIENT
    role = care_role_for(user)
    return MessageSenderRole.NURSE if role == "NURSE" else MessageSenderRole.PHYSICIAN


def get_or_create_thread(*, patient: User, participant_id: Any) -> tuple[MessageThread, bool]:
    participant = is_in_care_team(patient, participant_id)
    if participant is None:
        raise NotInCareTeam()
    existing = MessageThread.objects.filter(patient=patient, participant=participant).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            return MessageThread.objects.create(patient=patient, participant=participant), True
    except IntegrityError:
        return MessageThread.objects.get(patient=patient, participant=participant), False


def send_message(*, thread: MessageThread, sender: User, body: str) -> Message:
    with transaction.atomic():
        message = Message.objects.create(
            thread=thread,
            sender=sender,
            sender_role=sender_role_for(thread, sender),
            body=clean_body(body),
        )
        MessageThread.objects.filter(pk=thread.pk).update(
            last_message_at=message.created_at, updated_at=message.created_at
        )
        thread.last_message_at = message.created_at
        recipient = thread.participant if sender.pk == thread.patient_id else thread.patient
        notify(recipient, NotificationType.NEW_MESSAGE, target_kind="thread", target_id=thread.pk)
    return message


def unread_for(thread: MessageThread, viewer_id: Any) -> int:
    return thread.messages.filter(read_at__isnull=True).exclude(sender_id=viewer_id).count()


def patient_unread_total(user: User) -> int:
    return (
        Message.objects.filter(thread__patient=user, read_at__isnull=True)
        .exclude(sender=user)
        .count()
    )


def mark_read(*, thread: MessageThread, reader: User, up_to_message_id: Any) -> int:
    anchor = thread.messages.filter(pk=up_to_message_id).first()
    if anchor is None:
        raise ValidationError({"up_to_message_id": ["Unknown message in this thread."]})
    return (
        thread.messages.filter(created_at__lte=anchor.created_at, read_at__isnull=True)
        .exclude(sender=reader)
        .update(read_at=timezone.now())
    )


def last_message(thread: MessageThread) -> Message | None:
    return thread.messages.order_by("-created_at").first()


def thread_payload(thread: MessageThread, *, viewer_id: Any) -> dict[str, Any]:
    participant = physician_summary(thread.participant)
    participant["role"] = care_role_for(thread.participant) or "PHYSICIAN"
    latest = last_message(thread)
    return {
        "id": thread.pk,
        "participant": participant,
        "last_message": (
            {
                "preview": latest.body[:PREVIEW_CHARS],
                "sent_at": latest.created_at,
                "sender_role": latest.sender_role,
            }
            if latest is not None
            else None
        ),
        "unread_count": unread_for(thread, viewer_id),
        "updated_at": thread.last_message_at or thread.created_at,
    }


def message_payload(message: Message, *, viewer_id: Any) -> dict[str, Any]:
    sender = message.sender
    if message.sender_role == MessageSenderRole.PATIENT:
        sender_data = {
            "id": sender.pk,
            "name": sender.get_full_name(),
            "role": message.sender_role,
            "avatar_url": None,
        }
    else:
        summary = physician_summary(sender)
        sender_data = {
            "id": sender.pk,
            "name": summary["name"],
            "role": message.sender_role,
            "avatar_url": summary["avatar_url"],
        }
    return {
        "id": message.pk,
        "thread_id": message.thread_id,
        "sender": sender_data,
        "body": message.body,
        "sent_at": message.created_at,
        "read_at": message.read_at,
        "is_mine": message.sender_id == viewer_id,
    }


def thread_etag(thread: MessageThread, query: str) -> str:
    stats = thread.messages.aggregate(last=Max("created_at"), read=Max("read_at"))
    count = thread.messages.count()
    raw = f"{thread.pk}:{count}:{stats['last']}:{stats['read']}:{query}"
    return '"' + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32] + '"'


def threads_matching(threads: list[MessageThread], search: str) -> list[MessageThread]:
    """Search participant name or decrypted message text (bounded per patient)."""

    needle = search.casefold()
    matched: list[MessageThread] = []
    for thread in threads:
        name = physician_summary(thread.participant)["name"].casefold()
        if needle in name or needle in thread.participant.get_full_name().casefold():
            matched.append(thread)
            continue
        if any(needle in message.body.casefold() for message in thread.messages.all()):
            matched.append(thread)
    return matched


def can_view_as_admin(user: Any) -> bool:
    return getattr(user, "role", None) == Role.ADMIN


# ---------------------------------------------------------------------------
# Clinician side (Part 4, section 8)
# ---------------------------------------------------------------------------


def is_clinician(user: Any) -> bool:
    """Verified physician or nurse (STAFF with a NURSE clinician profile)."""

    role = getattr(user, "role", None)
    if role == Role.PHYSICIAN:
        return True
    return role == Role.STAFF and care_role_for(user) == "NURSE"


def clinician_patients(user: User) -> Any:
    from apps.patients.models import PatientProfile

    if user.role == Role.PHYSICIAN:
        from apps.clinical.treatments import care_patients

        return care_patients(user)
    patient_ids = PatientProfile.objects.filter(assigned_nurse=user).values("user_id")
    return User.objects.filter(role=Role.PATIENT, pk__in=patient_ids)


def clinician_unread_total(user: User) -> int:
    return (
        Message.objects.filter(thread__participant=user, read_at__isnull=True)
        .exclude(sender=user)
        .count()
    )


def get_or_create_clinician_thread(
    *, clinician: User, patient_id: Any
) -> tuple[MessageThread, bool]:
    patient = clinician_patients(clinician).filter(pk=patient_id).first()
    if patient is None:
        raise NotInCareTeam()
    existing = MessageThread.objects.filter(patient=patient, participant=clinician).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            return MessageThread.objects.create(patient=patient, participant=clinician), True
    except IntegrityError:
        return MessageThread.objects.get(patient=patient, participant=clinician), False


def clinician_thread_payload(thread: MessageThread, *, viewer_id: Any) -> dict[str, Any]:
    from apps.clinical.evaluations import patient_brief

    payload = thread_payload(thread, viewer_id=viewer_id)
    brief = patient_brief(thread.patient)
    payload["participant"] = {
        "id": thread.patient_id,
        "name": brief["name"],
        "role": "PATIENT",
        "age_years": brief["age_years"],
        "avatar_url": None,
    }
    return payload


def patient_context(thread: MessageThread) -> dict[str, Any]:
    from apps.consultations.models import OPEN_CONSULTATION_STATUSES, Consultation
    from apps.treatments.models import Treatment, TreatmentStatus

    treatments = Treatment.objects.filter(patient=thread.patient, status=TreatmentStatus.ACTIVE)
    next_consultation = (
        Consultation.objects.select_related("consultation_type", "physician__clinician_profile")
        .filter(
            patient=thread.patient,
            status__in=OPEN_CONSULTATION_STATUSES,
            end__gte=timezone.now(),
        )
        .order_by("start")
        .first()
    )
    return {
        "patient_id": thread.patient_id,
        "active_treatments": [
            {"treatment_id": item.pk, "medication_name": item.medication_name}
            for item in treatments
        ],
        "next_consultation": next_consultation,
    }


def patient_name_matches(threads: list[MessageThread], search: str) -> list[MessageThread]:
    needle = search.casefold()
    return [thread for thread in threads if needle in thread.patient.get_full_name().casefold()]


__all__ = [
    "can_view_as_admin",
    "clean_body",
    "clinician_patients",
    "clinician_thread_payload",
    "clinician_unread_total",
    "get_or_create_clinician_thread",
    "get_or_create_thread",
    "is_clinician",
    "mark_read",
    "message_payload",
    "patient_context",
    "patient_name_matches",
    "patient_unread_total",
    "send_message",
    "thread_etag",
    "thread_payload",
    "threads_matching",
    "unread_for",
]
