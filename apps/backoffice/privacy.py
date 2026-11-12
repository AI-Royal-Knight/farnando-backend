"""Privacy requests (Part 5, 11.2): access exports and anonymisation."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import ConsentRecord, User
from apps.backoffice.models import PrivacyRequest, PrivacyRequestStatus, PrivacyRequestType
from apps.core.audit import record_audit
from apps.core.exceptions import InvalidStateTransition
from apps.core.storage import presigned_url

OPEN_STATES = (PrivacyRequestStatus.RECEIVED, PrivacyRequestStatus.IN_PROGRESS)
RETAINED_RECORDS = [
    "payments",
    "orders",
    "prescriptions",
    "treatments",
    "complaints",
    "audit trail",
]


def _notify(privacy_request: PrivacyRequest) -> None:
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    user = privacy_request.user
    transaction.on_commit(
        lambda: notify(
            user,
            NotificationType.PRIVACY_REQUEST_UPDATED,
            target_kind="PRIVACY_REQUEST",
            target_id=privacy_request.pk,
        )
    )


def download_url(privacy_request: PrivacyRequest) -> tuple[str | None, Any]:
    expires = privacy_request.export_expires_at
    if not privacy_request.export_key or expires is None or expires <= timezone.now():
        return None, None
    ttl = max(1, int((expires - timezone.now()).total_seconds()))
    return presigned_url(privacy_request.export_key, expires_in=ttl), expires


def patient_payload(privacy_request: PrivacyRequest) -> dict[str, Any]:
    url, expires = download_url(privacy_request)
    return {
        "id": privacy_request.pk,
        "type": privacy_request.type,
        "status": privacy_request.status,
        "details": privacy_request.details or None,
        "response_note": privacy_request.response_note or None,
        "due_at": privacy_request.due_at,
        "download_url": url,
        "download_expires_at": expires,
        "created_at": privacy_request.created_at,
        "updated_at": privacy_request.updated_at,
    }


def admin_payload(privacy_request: PrivacyRequest) -> dict[str, Any]:
    body = patient_payload(privacy_request)
    user = privacy_request.user
    handler = privacy_request.handled_by
    body.update(
        {
            "patient": {"id": user.pk, "name": user.get_full_name(), "email": user.email},
            "completed_at": privacy_request.completed_at,
            "is_overdue": privacy_request.status in OPEN_STATES
            and privacy_request.due_at < timezone.now(),
            "handled_by": {"id": handler.pk, "name": handler.get_full_name()} if handler else None,
        }
    )
    return body


def create(*, user: User, data: dict[str, Any], request: Any) -> PrivacyRequest:
    privacy_request = PrivacyRequest.objects.create(
        user=user,
        type=data["type"],
        details=strip_tags(data.get("details") or "").strip(),
        due_at=timezone.now() + timedelta(days=int(settings.PRIVACY_RESPONSE_DAYS)),
    )
    record_audit(
        actor=user,
        action="privacy_request.created",
        object_type="PrivacyRequest",
        object_id=privacy_request.pk,
        request=request,
        patient_id=user.pk,
    )
    from apps.clinical.tasks import send_staff_alert

    transaction.on_commit(lambda: send_staff_alert.delay("privacy_request_received"))
    return privacy_request


def for_patient(user: User) -> QuerySet[PrivacyRequest]:
    return PrivacyRequest.objects.filter(user=user).order_by("-created_at")


def filtered(params: Any) -> QuerySet[PrivacyRequest]:
    queryset = PrivacyRequest.objects.select_related("user", "handled_by")
    if params.get("status"):
        queryset = queryset.filter(status=params["status"])
    if params.get("type"):
        queryset = queryset.filter(type=params["type"])
    return queryset.order_by("due_at")


def get(pk: Any, *, actor: User, request: Any) -> PrivacyRequest:
    privacy_request = filtered({}).filter(pk=pk).first()
    if privacy_request is None:
        raise NotFound()
    record_audit(
        actor=actor,
        action="privacy_request.viewed",
        object_type="PrivacyRequest",
        object_id=privacy_request.pk,
        request=request,
        patient_id=privacy_request.user_id,
    )
    return privacy_request


def set_status(
    *, privacy_request: PrivacyRequest, status: str, note: str, actor: User, request: Any
) -> PrivacyRequest:
    if privacy_request.status not in OPEN_STATES:
        raise InvalidStateTransition("The request is already closed.")
    if status == privacy_request.status:
        raise InvalidStateTransition()
    if status == PrivacyRequestStatus.REJECTED and not note.strip():
        raise ValidationError({"note": ["Required when rejecting."]})
    if status == PrivacyRequestStatus.RECEIVED:
        raise InvalidStateTransition()
    privacy_request.status = status
    if note:
        privacy_request.response_note = strip_tags(note).strip()
    privacy_request.handled_by = actor
    if status in (PrivacyRequestStatus.COMPLETED, PrivacyRequestStatus.REJECTED):
        privacy_request.completed_at = timezone.now()
    privacy_request.save()
    record_audit(
        actor=actor,
        action="privacy_request.updated",
        object_type="PrivacyRequest",
        object_id=privacy_request.pk,
        request=request,
        patient_id=privacy_request.user_id,
        changed_fields=["status"],
    )
    _notify(privacy_request)
    return privacy_request


# ---------------------------------------------------------------------------
# Fulfilment
# ---------------------------------------------------------------------------


def build_export(user: User) -> dict[str, Any]:
    """Machine-readable copy of the patient's own data."""

    profile = getattr(user, "patient_profile", None)
    data: dict[str, Any] = {
        "generated_at": timezone.now(),
        "account": {
            "id": user.pk,
            "email": user.email,
            "full_name": user.full_name,
            "date_joined": user.date_joined,
            "last_login": user.last_login,
        },
        "profile": None,
        "addresses": [
            {
                "label": address.label,
                "recipient_name": address.recipient_name,
                "phone": address.phone,
                "line1": address.line1,
                "line2": address.line2,
                "reference": address.reference,
                "ubigeo": address.ubigeo_id,
                "postal_code": address.postal_code,
                "country": address.country,
            }
            for address in user.addresses.all()
        ],
        "consents": [
            {
                "document_type": record.document_type,
                "version": record.version,
                "accepted_at": record.accepted_at,
            }
            for record in ConsentRecord.objects.filter(user=user)
        ],
        "assessments": [],
        "evaluations": [],
        "orders": [],
        "subscriptions": [],
        "consultations": [],
        "payments": [],
    }
    if profile is not None:
        data["profile"] = {
            "phone": profile.phone,
            "language": profile.language,
            "timezone": profile.timezone,
        }
    from apps.assessments.models import Assessment, Evaluation, Payment
    from apps.consultations.models import Consultation
    from apps.orders.models import Order, Subscription

    for assessment in Assessment.objects.filter(user=user).prefetch_related("answers"):
        data["assessments"].append(
            {
                "id": assessment.pk,
                "program": assessment.program_id,
                "status": assessment.status,
                "submitted_at": assessment.submitted_at,
                "answers": [
                    {
                        "question": answer.question_id,
                        "option_ids": list(answer.option_ids or []),
                        "answer": answer.answer or None,
                        "text": answer.text or None,
                    }
                    for answer in assessment.answers.all()
                ],
            }
        )
    for evaluation in Evaluation.objects.filter(assessment__user=user):
        data["evaluations"].append(
            {
                "id": evaluation.pk,
                "status": evaluation.status,
                "submitted_at": evaluation.submitted_at,
                "decided_at": evaluation.decided_at,
            }
        )
    for order in Order.objects.filter(user=user):
        data["orders"].append(
            {
                "number": order.number,
                "status": order.status,
                "placed_at": order.placed_at,
                "total": f"{order.total:.2f}",
                "currency": order.currency,
            }
        )
    for subscription in Subscription.objects.filter(user=user).select_related("package"):
        data["subscriptions"].append(
            {
                "id": subscription.pk,
                "package": subscription.package.name,
                "status": subscription.status,
                "created_at": subscription.created_at,
                "cancelled_at": subscription.cancelled_at,
            }
        )
    for consultation in Consultation.objects.filter(patient=user):
        data["consultations"].append(
            {
                "id": consultation.pk,
                "status": consultation.status,
                "start": consultation.start,
            }
        )
    for payment in Payment.objects.filter(user=user):
        data["payments"].append(
            {
                "id": payment.pk,
                "purpose": payment.purpose,
                "status": payment.status,
                "amount": f"{payment.amount:.2f}",
                "currency": payment.currency,
                "created_at": payment.created_at,
            }
        )
    return data


def anonymise(user: User) -> None:
    """Remove direct identifiers; keep records the law requires."""

    from apps.accounts.services import revoke_all_refresh_tokens

    token = user.pk.hex[:12]
    user.full_name = "Deleted user"
    user.first_name = ""
    user.last_name = ""
    user.email = f"deleted-{token}@deleted.invalid"
    user.is_active = False
    user.set_unusable_password()
    user.save()
    profile = getattr(user, "patient_profile", None)
    if profile is not None:
        profile.phone = ""
        profile.email_marketing = False
        profile.save()
    user.addresses.update(
        recipient_name="Deleted user", phone="", line1="Deleted", line2="", reference=""
    )
    # Stop all future charges: cancel open subscriptions now and drop saved cards.
    from apps.orders.models import OPEN_SUBSCRIPTION_STATUSES, Subscription, SubscriptionStatus

    now = timezone.now()
    Subscription.objects.filter(user=user, status__in=OPEN_SUBSCRIPTION_STATUSES).update(
        status=SubscriptionStatus.CANCELLED,
        cancelled_at=now,
        cancel_at_period_end=False,
        next_billing_at=None,
        next_retry_at=None,
        cancellation_reason="Account deleted (privacy request).",
        updated_at=now,
    )
    user.payment_methods.filter(is_deleted=False).update(
        is_deleted=True, is_default=False, deleted_at=now, updated_at=now
    )
    # allauth keeps its own copies of the address and the Google identity.
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    EmailAddress.objects.filter(user=user).delete()
    SocialAccount.objects.filter(user=user).delete()
    user.mfa_backup_codes.all().delete()
    user.mfa_challenges.all().delete()
    if hasattr(user, "mfa_device"):
        user.mfa_device.delete()
    revoke_all_refresh_tokens(user)


def fulfill(*, privacy_request: PrivacyRequest, actor: User, request: Any) -> PrivacyRequest:
    if privacy_request.status not in OPEN_STATES:
        raise InvalidStateTransition("The request is already closed.")
    if privacy_request.type == PrivacyRequestType.RECTIFICATION:
        raise InvalidStateTransition(
            "Rectification is handled through the support process; set the status instead."
        )
    with transaction.atomic():
        if privacy_request.type == PrivacyRequestType.ACCESS_EXPORT:
            payload = json.dumps(build_export(privacy_request.user), cls=DjangoJSONEncoder)
            key = default_storage.save(
                f"exports/privacy/{privacy_request.pk}.json", ContentFile(payload.encode())
            )
            privacy_request.export_key = key
            privacy_request.export_expires_at = timezone.now() + timedelta(
                days=int(settings.PRIVACY_EXPORT_TTL_DAYS)
            )
            action = "privacy_request.exported"
        else:
            anonymise(privacy_request.user)
            privacy_request.response_note = (
                "Your account was anonymised. These records are kept because the law "
                "requires it: " + ", ".join(RETAINED_RECORDS) + "."
            )
            action = "privacy_request.anonymised"
        privacy_request.status = PrivacyRequestStatus.COMPLETED
        privacy_request.completed_at = timezone.now()
        privacy_request.handled_by = actor
        privacy_request.save()
        record_audit(
            actor=actor,
            action=action,
            object_type="PrivacyRequest",
            object_id=privacy_request.pk,
            request=request,
            patient_id=privacy_request.user_id,
        )
        if privacy_request.type == PrivacyRequestType.ACCESS_EXPORT:
            _notify(privacy_request)
    return privacy_request
