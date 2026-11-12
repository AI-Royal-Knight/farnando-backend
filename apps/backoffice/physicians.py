"""Physician management for admins (contract Part 5, 2.3)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.enums import EvaluationStatus
from apps.assessments.models import Evaluation
from apps.assessments.services import EvaluationService
from apps.clinical.models import InvitationStatus, PhysicianInvitation, Specialty
from apps.clinical.onboarding import (
    avatar_url_for,
    create_invitation,
    notify_verification_change,
)
from apps.clinical.permissions import physician_profile
from apps.consultations.models import (
    ClinicianProfile,
    Consultation,
    ConsultationStatus,
    PhysicianVerificationStatus,
)
from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, InvalidStateTransition, PhysicianNotEligible
from apps.staff.accounts import reactivate_user, suspend_user
from apps.treatments.models import Treatment, TreatmentStatus

OPEN_EVALUATION_STATUSES = (
    EvaluationStatus.SUBMITTED,
    EvaluationStatus.IN_REVIEW,
    EvaluationStatus.PENDING_LABS,
)


def physicians() -> QuerySet[User]:
    return User.objects.filter(role=Role.PHYSICIAN).select_related(
        "clinician_profile__specialty_ref"
    )


def get_physician(physician_id: Any) -> User:
    user = physicians().filter(pk=physician_id).first()
    if user is None:
        raise NotFound()
    physician_profile(user)
    return user


def list_physicians(*, search: str, status: str, specialty_id: Any) -> QuerySet[User]:
    queryset = physicians().order_by("full_name", "email")
    if status == "INVITED":
        return queryset.none()
    if search:
        queryset = queryset.filter(
            Q(full_name__icontains=search)
            | Q(email__icontains=search)
            | Q(clinician_profile__display_name__icontains=search)
        )
    if status:
        queryset = queryset.filter(clinician_profile__verification_status=status)
    if specialty_id:
        queryset = queryset.filter(clinician_profile__specialty_ref_id=specialty_id)
    return queryset


def _month_start() -> Any:
    local = timezone.localtime()
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def card_payload(user: User) -> dict[str, Any]:
    profile = physician_profile(user)
    specialty = profile.specialty_ref
    assigned = (
        Evaluation.objects.filter(status__in=OPEN_EVALUATION_STATUSES)
        .filter(Q(physician=user) | Q(assigned_physician=user, physician__isnull=True))
        .count()
    )
    active_patients = (
        Treatment.objects.filter(prescribing_physician=user, status=TreatmentStatus.ACTIVE)
        .values("patient_id")
        .distinct()
        .count()
    )
    consultations = (
        Consultation.objects.filter(physician=user, start__gte=_month_start())
        .exclude(
            status__in=(
                ConsultationStatus.CANCELLED,
                ConsultationStatus.DECLINED,
                ConsultationStatus.EXPIRED,
            )
        )
        .count()
    )
    return {
        "id": user.pk,
        "full_name": user.full_name or profile.display_name,
        "avatar_url": avatar_url_for(profile),
        "specialty": {"id": specialty.pk, "name": specialty.name} if specialty else None,
        "headline": profile.headline,
        "verification_status": profile.verification_status,
        "account_status": "ACTIVE" if user.is_active else "SUSPENDED",
        "stats": {
            "assigned_evaluations_count": assigned,
            "active_patients_count": active_patients,
            "consultations_this_month": consultations,
        },
        "last_active_at": user.last_login,
    }


def average_review_hours(user: User) -> float | None:
    since = timezone.now() - timedelta(days=30)
    result = (
        Evaluation.objects.filter(physician=user, decided_at__gte=since)
        .annotate(
            duration=ExpressionWrapper(
                F("decided_at") - F("submitted_at"), output_field=DurationField()
            )
        )
        .aggregate(avg=Avg("duration"), n=Count("id"))
    )
    if not result["n"] or result["avg"] is None:
        return None
    return float(round(result["avg"].total_seconds() / 3600, 1))


def detail_payload(user: User) -> dict[str, Any]:
    profile = physician_profile(user)
    invitation = (
        PhysicianInvitation.objects.filter(accepted_user=user).select_related("invited_by").first()
    )
    inviter = invitation.invited_by if invitation else None
    body = card_payload(user)
    body.update(
        {
            "license_number": profile.license_number,
            "specialty_registration_number": profile.specialty_registration_number or None,
            "email": user.email,
            "phone": profile.phone or None,
            "verification_note": profile.verification_note or None,
            "invited_by": (
                {"id": inviter.pk, "name": inviter.get_full_name()} if inviter else None
            ),
            "accepting_new_consultations": profile.accepts_new_consultations,
            "average_review_hours": average_review_hours(user),
        }
    )
    return body


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def invite(*, email: str, admin: User, request: Any) -> PhysicianInvitation:
    email = User.objects.normalize_email(email)
    with transaction.atomic():
        if User.objects.filter(email=email).exists():
            raise Conflict("An account already exists for this email.")
        pending = PhysicianInvitation.objects.filter(
            email=email, status=InvitationStatus.PENDING, expires_at__gt=timezone.now()
        )
        if pending.exists():
            raise Conflict("A pending invitation already exists for this email.")
        invitation, _token = create_invitation(email=email, invited_by=admin)
        record_audit(
            actor=admin,
            action="physician.invited",
            object_type="PhysicianInvitation",
            object_id=invitation.pk,
            request=request,
        )
        _queue(invitation)
    return invitation


def _queue(invitation: PhysicianInvitation) -> None:
    from apps.staff.tasks import send_physician_invitation

    invitation_id = str(invitation.pk)
    transaction.on_commit(lambda: send_physician_invitation.delay(invitation_id))


def _pending(invitation_id: Any) -> PhysicianInvitation:
    invitation = PhysicianInvitation.objects.filter(pk=invitation_id).first()
    if invitation is None:
        raise NotFound()
    if invitation.status != InvitationStatus.PENDING:
        raise InvalidStateTransition()
    return invitation


def resend(*, invitation_id: Any, admin: User, request: Any) -> PhysicianInvitation:
    invitation = _pending(invitation_id)
    record_audit(
        actor=admin,
        action="physician.invitation_resent",
        object_type="PhysicianInvitation",
        object_id=invitation.pk,
        request=request,
    )
    _queue(invitation)
    return invitation


def revoke(*, invitation_id: Any, admin: User, request: Any) -> PhysicianInvitation:
    invitation = _pending(invitation_id)
    invitation.status = InvitationStatus.REVOKED
    invitation.save(update_fields=["status", "updated_at"])
    record_audit(
        actor=admin,
        action="physician.invitation_revoked",
        object_type="PhysicianInvitation",
        object_id=invitation.pk,
        request=request,
    )
    return invitation


def invitation_payload(invitation: PhysicianInvitation) -> dict[str, Any]:
    status = invitation.status
    if status == InvitationStatus.PENDING and invitation.expires_at <= timezone.now():
        status = InvitationStatus.EXPIRED
    return {
        "id": invitation.pk,
        "email": invitation.email,
        "status": status,
        "expires_at": invitation.expires_at,
        "created_at": invitation.created_at,
    }


# ---------------------------------------------------------------------------
# Verification lifecycle
# ---------------------------------------------------------------------------


def _set_status(
    user: User,
    *,
    expected: tuple[str, ...],
    target: str,
    admin: User,
    request: Any,
    note: str = "",
    action: str,
) -> ClinicianProfile:
    profile = physician_profile(user)
    if profile.verification_status not in expected:
        raise InvalidStateTransition()
    profile.verification_status = target
    fields = ["verification_status", "updated_at"]
    if note:
        profile.verification_note = strip_tags(note).strip()
        fields.append("verification_note")
    profile.save(update_fields=fields)
    record_audit(
        actor=admin,
        action=action,
        object_type="User",
        object_id=user.pk,
        request=request,
        changed_fields=["verification_status"],
    )
    return profile


def verify(*, user: User, note: str, admin: User, request: Any) -> None:
    with transaction.atomic():
        profile = _set_status(
            user,
            expected=(PhysicianVerificationStatus.PENDING,),
            target=PhysicianVerificationStatus.VERIFIED,
            admin=admin,
            request=request,
            note=note,
            action="physician.verified",
        )
        notify_verification_change(profile)


def reject(*, user: User, reason: str, admin: User, request: Any) -> None:
    with transaction.atomic():
        profile = _set_status(
            user,
            expected=(PhysicianVerificationStatus.PENDING,),
            target=PhysicianVerificationStatus.REJECTED,
            admin=admin,
            request=request,
            note=reason,
            action="physician.rejected",
        )
        notify_verification_change(profile)


def is_eligible(physician: User, program: Any = None) -> bool:
    profile = ClinicianProfile.objects.filter(user=physician).first()
    if (
        profile is None
        or physician.role != Role.PHYSICIAN
        or not physician.is_active
        or profile.verification_status != PhysicianVerificationStatus.VERIFIED
        or not profile.accepts_new_consultations
    ):
        return False
    if program is None:
        return True
    allowed = list(program.allowed_specialties.values_list("pk", flat=True))
    return not allowed or profile.specialty_ref_id in allowed


def eligible_physician(physician_id: Any, program: Any = None) -> User:
    physician = User.objects.filter(pk=physician_id, role=Role.PHYSICIAN).first()
    if physician is None or not is_eligible(physician, program):
        raise PhysicianNotEligible()
    return cast(User, physician)


def suspend(*, user: User, reason: str, reassign_to: Any, admin: User, request: Any) -> None:
    from apps.clinical.tasks import send_staff_alert

    with transaction.atomic():
        target = None
        if reassign_to:
            target = eligible_physician(reassign_to)
            if target.pk == user.pk:
                raise ValidationError({"reassign_to_physician_id": ["Choose another physician."]})
        _set_status(
            user,
            expected=(PhysicianVerificationStatus.VERIFIED,),
            target=PhysicianVerificationStatus.SUSPENDED,
            admin=admin,
            request=request,
            note=reason,
            action="physician.suspended",
        )
        suspend_user(user, reason=reason)
        for evaluation in Evaluation.objects.select_for_update().filter(
            Q(physician=user) | Q(assigned_physician=user),
            status__in=OPEN_EVALUATION_STATUSES,
        ):
            evaluation.assigned_physician = target
            if evaluation.physician_id == user.pk:
                evaluation.physician = None
                evaluation.claimed_at = None
            evaluation.save(
                update_fields=["assigned_physician", "physician", "claimed_at", "updated_at"]
            )
            if evaluation.status == EvaluationStatus.IN_REVIEW:
                EvaluationService.transition(
                    evaluation, EvaluationStatus.SUBMITTED, actor=admin, reason="Reassigned"
                )
        flagged = Consultation.objects.filter(
            physician=user, status=ConsultationStatus.CONFIRMED, start__gte=timezone.now()
        ).update(needs_staff_attention=True)
        if flagged:
            transaction.on_commit(lambda: send_staff_alert.delay("physician_suspended"))


def reactivate(*, user: User, admin: User, request: Any) -> None:
    with transaction.atomic():
        _set_status(
            user,
            expected=(PhysicianVerificationStatus.SUSPENDED,),
            target=PhysicianVerificationStatus.VERIFIED,
            admin=admin,
            request=request,
            action="physician.reactivated",
        )
        reactivate_user(user)


def update_credentials(*, user: User, data: dict[str, Any], admin: User, request: Any) -> None:
    profile = physician_profile(user)
    changed: list[str] = []
    with transaction.atomic():
        if "full_name" in data:
            user.full_name = data["full_name"].strip()
            user.save(update_fields=["full_name"])
            profile.display_name = user.full_name
            changed.append("full_name")
        if "license_number" in data:
            number = data["license_number"].strip().upper()
            clash = ClinicianProfile.objects.filter(license_number__iexact=number).exclude(
                pk=profile.pk
            )
            if clash.exists():
                raise ValidationError(
                    {"license_number": ["This license number is already registered."]}
                )
            profile.license_number = number
            profile.license_label = f"CMP {number}"
            changed.append("license_number")
        if "specialty_id" in data:
            specialty = Specialty.objects.filter(pk=data["specialty_id"]).first()
            if specialty is None:
                raise ValidationError({"specialty_id": ["Unknown specialty."]})
            profile.specialty_ref = specialty
            profile.specialty = specialty.name
            changed.append("specialty_id")
        if "specialty_registration_number" in data:
            profile.specialty_registration_number = (
                (data["specialty_registration_number"] or "").strip().upper()
            )
            changed.append("specialty_registration_number")
        specialty = profile.specialty_ref
        if (
            specialty is not None
            and specialty.requires_registration_number
            and not profile.specialty_registration_number
        ):
            raise ValidationError(
                {"specialty_registration_number": ["Required for this specialty."]}
            )
        profile.save()
        record_audit(
            actor=admin,
            action="physician.credentials_updated",
            object_type="User",
            object_id=user.pk,
            request=request,
            changed_fields=changed,
            metadata={"reason": strip_tags(data["reason"]).strip()},
        )
