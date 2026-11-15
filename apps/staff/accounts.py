"""Staff accounts and invitations (contract Part 5, 2.1 and 2.2)."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.accounts.services import ConsentService, revoke_all_refresh_tokens
from apps.clinical.onboarding import hash_token
from apps.consultations.clinicians import avatar_url, profile_for
from apps.core.audit import record_audit
from apps.core.exceptions import (
    Conflict,
    InvalidStateTransition,
    InvitationAlreadyUsed,
    InvitationExpired,
    InvitationRevoked,
)
from apps.staff.models import (
    DEFAULT_BUNDLES,
    AccountStatus,
    StaffInvitation,
    StaffInvitationStatus,
    StaffProfile,
)
from apps.staff.permissions import MissingPermission, staff_profile


def _self_guard(actor: User, target: User) -> None:
    if actor.pk == target.pk:
        raise MissingPermission(details={"reason": "self"})


# ---------------------------------------------------------------------------
# Generic account suspension (staff, physicians and patients)
# ---------------------------------------------------------------------------


def suspend_user(user: User, *, reason: str) -> None:
    user.is_active = False
    user.suspended_at = timezone.now()
    user.suspension_reason = strip_tags(reason or "").strip()
    user.save(update_fields=["is_active", "suspended_at", "suspension_reason"])
    revoke_all_refresh_tokens(user)


def reactivate_user(user: User) -> None:
    user.is_active = True
    user.suspended_at = None
    user.suspension_reason = ""
    user.save(update_fields=["is_active", "suspended_at", "suspension_reason"])


# ---------------------------------------------------------------------------
# Read model
# ---------------------------------------------------------------------------


def staff_queryset() -> QuerySet[User]:
    return User.objects.filter(role=Role.STAFF).select_related("staff_profile", "clinician_profile")


def ensure_profile(user: User) -> StaffProfile:
    profile = StaffProfile.objects.filter(user=user).first()
    if profile is None:
        profile = StaffProfile.objects.create(
            user=user, staff_type="SUPPORT_AGENT", permissions=[], status=AccountStatus.ACTIVE
        )
    return profile


def member_payload(user: User) -> dict[str, Any]:
    profile = ensure_profile(user)
    return {
        "id": user.pk,
        "full_name": user.full_name,
        "email": user.email,
        "phone": profile.phone or None,
        "staff_type": profile.staff_type,
        "permissions": sorted(profile.permissions),
        "status": profile.status,
        "avatar_url": avatar_url(profile_for(user)),
        "last_login_at": user.last_login,
        "created_at": user.date_joined,
    }


def list_staff(*, search: str = "", staff_type: str = "", status: str = "") -> QuerySet[User]:
    queryset = staff_queryset().order_by("full_name", "email")
    if search:
        queryset = queryset.filter(Q(full_name__icontains=search) | Q(email__icontains=search))
    if staff_type:
        queryset = queryset.filter(staff_profile__staff_type=staff_type)
    if status:
        queryset = queryset.filter(staff_profile__status=status)
    return queryset


def get_member(member_id: Any) -> User:
    user = staff_queryset().filter(pk=member_id).first()
    if user is None:
        raise NotFound()
    return user


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------


def _email_taken(email: str) -> bool:
    return User.objects.filter(email=email).exists()


def _queue_invitation_email(invitation: StaffInvitation) -> None:
    from apps.staff.tasks import send_staff_invitation

    invitation_id = str(invitation.pk)
    transaction.on_commit(lambda: send_staff_invitation.delay(invitation_id))


def create_staff(*, data: dict[str, Any], admin: User, request: Any) -> User:
    email = User.objects.normalize_email(data["email"])
    staff_type = data["staff_type"]
    permissions = data.get("permissions")
    if permissions is None:
        permissions = list(DEFAULT_BUNDLES[staff_type])
    with transaction.atomic():
        if _email_taken(email):
            raise ValidationError({"email": ["An account already exists for this email."]})
        user = User.objects.create_user(
            email=email,
            password=None,
            full_name=data["full_name"].strip(),
            role=Role.STAFF,
            is_active=False,
            is_email_verified=False,
        )
        StaffProfile.objects.create(
            user=user,
            staff_type=staff_type,
            permissions=sorted(set(permissions)),
            status=AccountStatus.INVITED,
            phone=data.get("phone") or "",
            invited_by=admin,
        )
        invitation = StaffInvitation.objects.create(
            user=user,
            email=email,
            full_name=user.full_name,
            staff_type=staff_type,
            token_hash=hash_token(secrets.token_urlsafe(32)),
            expires_at=timezone.now() + timedelta(days=settings.STAFF_INVITATION_TTL_DAYS),
            invited_by=admin,
        )
        record_audit(
            actor=admin,
            action="staff.invited",
            object_type="User",
            object_id=user.pk,
            request=request,
            metadata={"staff_type": staff_type, "permissions": sorted(set(permissions))},
        )
        _queue_invitation_email(invitation)
    return user


def update_staff(*, user: User, data: dict[str, Any], admin: User, request: Any) -> User:
    profile = ensure_profile(user)
    changed: list[str] = []
    with transaction.atomic():
        if "permissions" in data:
            _self_guard(admin, user)
            if not data.get("reason"):
                raise ValidationError({"reason": ["A reason is required to change permissions."]})
            new_permissions = sorted(set(data["permissions"]))
            if new_permissions != sorted(profile.permissions):
                record_audit(
                    actor=admin,
                    action="staff.permissions_changed",
                    object_type="User",
                    object_id=user.pk,
                    request=request,
                    changed_fields=["permissions"],
                    metadata={
                        "from": sorted(profile.permissions),
                        "to": new_permissions,
                        "reason": strip_tags(data["reason"]).strip(),
                    },
                )
                profile.permissions = new_permissions
                changed.append("permissions")
        if "staff_type" in data and data["staff_type"] != profile.staff_type:
            profile.staff_type = data["staff_type"]
            changed.append("staff_type")
        if "phone" in data:
            profile.phone = data["phone"] or ""
            changed.append("phone")
        if "full_name" in data:
            user.full_name = data["full_name"].strip()
            user.save(update_fields=["full_name"])
            changed.append("full_name")
        profile.save()
        if changed:
            record_audit(
                actor=admin,
                action="staff.updated",
                object_type="User",
                object_id=user.pk,
                request=request,
                changed_fields=changed,
            )
    return user


def suspend_staff(*, user: User, reason: str, admin: User, request: Any) -> User:
    _self_guard(admin, user)
    profile = ensure_profile(user)
    if profile.status == AccountStatus.SUSPENDED:
        raise InvalidStateTransition()
    with transaction.atomic():
        suspend_user(user, reason=reason)
        if profile.status == AccountStatus.INVITED:
            StaffInvitation.objects.filter(user=user, status=StaffInvitationStatus.PENDING).update(
                status=StaffInvitationStatus.REVOKED
            )
        profile.status = AccountStatus.SUSPENDED
        profile.save(update_fields=["status", "updated_at"])
        record_audit(
            actor=admin,
            action="staff.suspended",
            object_type="User",
            object_id=user.pk,
            request=request,
        )
    return user


def reactivate_staff(*, user: User, admin: User, request: Any) -> User:
    _self_guard(admin, user)
    profile = ensure_profile(user)
    if profile.status != AccountStatus.SUSPENDED:
        raise InvalidStateTransition()
    with transaction.atomic():
        has_password = user.has_usable_password()
        if has_password:
            reactivate_user(user)
            profile.status = AccountStatus.ACTIVE
        else:
            # Never accepted the invitation: issue a fresh one.
            profile.status = AccountStatus.INVITED
            user.suspended_at = None
            user.suspension_reason = ""
            user.save(update_fields=["suspended_at", "suspension_reason"])
            invitation = StaffInvitation.objects.create(
                user=user,
                email=user.email,
                full_name=user.full_name,
                staff_type=profile.staff_type,
                token_hash=hash_token(secrets.token_urlsafe(32)),
                expires_at=timezone.now() + timedelta(days=settings.STAFF_INVITATION_TTL_DAYS),
                invited_by=admin,
            )
            _queue_invitation_email(invitation)
        profile.save(update_fields=["status", "updated_at"])
        record_audit(
            actor=admin,
            action="staff.reactivated",
            object_type="User",
            object_id=user.pk,
            request=request,
        )
    return user


def _pending_invitation(user: User) -> StaffInvitation:
    invitation = (
        StaffInvitation.objects.filter(user=user, status=StaffInvitationStatus.PENDING)
        .order_by("-created_at")
        .first()
    )
    if invitation is None:
        raise InvalidStateTransition()
    return invitation


def resend_invitation(*, user: User, admin: User, request: Any) -> User:
    if ensure_profile(user).status != AccountStatus.INVITED:
        raise InvalidStateTransition()
    invitation = _pending_invitation(user)
    record_audit(
        actor=admin,
        action="staff.invitation_resent",
        object_type="User",
        object_id=user.pk,
        request=request,
    )
    _queue_invitation_email(invitation)
    return user


def delete_invited(*, user: User, admin: User, request: Any) -> None:
    if ensure_profile(user).status != AccountStatus.INVITED:
        raise Conflict("Only invited accounts can be deleted. Suspend active accounts.")
    with transaction.atomic():
        StaffInvitation.objects.filter(user=user).update(
            status=StaffInvitationStatus.REVOKED, user=None
        )
        record_audit(
            actor=admin,
            action="staff.invitation_revoked",
            object_type="User",
            object_id=user.pk,
            request=request,
            metadata={"email": user.email},
        )
        user.delete()


# ---------------------------------------------------------------------------
# Public acceptance (2.2)
# ---------------------------------------------------------------------------


def resolve_staff_invitation(token: str, *, lock: bool = False) -> StaffInvitation:
    queryset = StaffInvitation.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    invitation = queryset.filter(token_hash=hash_token(token)).first()
    if invitation is None:
        raise NotFound()
    if invitation.status == StaffInvitationStatus.REVOKED or invitation.user_id is None:
        raise InvitationRevoked()
    if invitation.status == StaffInvitationStatus.ACCEPTED:
        raise InvitationAlreadyUsed()
    if (
        invitation.status == StaffInvitationStatus.EXPIRED
        or invitation.expires_at <= timezone.now()
    ):
        if invitation.status != StaffInvitationStatus.EXPIRED:
            invitation.status = StaffInvitationStatus.EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
        raise InvitationExpired()
    return invitation


def accept_staff_invitation(*, token: str, data: dict[str, Any], request: Any) -> User:
    from django.contrib.auth.password_validation import validate_password

    with transaction.atomic():
        invitation = resolve_staff_invitation(token, lock=True)
        user: User = User.objects.select_for_update().get(pk=invitation.user_id)
        if data["password"] != data["confirm_password"]:
            raise ValidationError({"confirm_password": ["Passwords do not match."]})
        try:
            validate_password(data["password"], user)
        except Exception as exc:
            messages = list(getattr(exc, "messages", [str(exc)]))
            raise ValidationError({"password": messages}) from exc
        ConsentService.validate_items(data["consents"], require_required=True)
        user.set_password(data["password"])
        user._accepting_staff_invitation = True  # type: ignore[attr-defined]  # see signals.py
        user.is_active = True
        user.is_email_verified = True
        user.email_verified_at = timezone.now()
        user.save()
        profile = ensure_profile(user)
        profile.status = AccountStatus.ACTIVE
        if data.get("phone"):
            profile.phone = data["phone"]
        profile.save()
        ConsentService.record_many(
            user=user,
            items=data["consents"],
            request=getattr(request, "_request", request),
            require_required=True,
        )
        invitation.status = StaffInvitationStatus.ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["status", "accepted_at", "updated_at"])
        record_audit(
            actor=user,
            action="staff.account_created",
            object_type="User",
            object_id=user.pk,
            request=request,
        )
    return user


def activate_invited_staff(user: User) -> bool:
    """Finish the invitation of a staff account that was activated some other way.

    Admins sometimes set the password and tick "active" in the Django admin instead of having
    the person accept the emailed invitation. The profile would then stay INVITED, which grants
    no permissions, so every staff endpoint answers 403. Returns True when it changed anything.
    """

    if user.role != Role.STAFF or not user.is_active or not user.has_usable_password():
        return False
    profile = staff_profile(user)
    if profile is None or profile.status != AccountStatus.INVITED:
        return False
    with transaction.atomic():
        profile.status = AccountStatus.ACTIVE
        profile.save(update_fields=["status", "updated_at"])
        StaffInvitation.objects.filter(user=user, status=StaffInvitationStatus.PENDING).update(
            status=StaffInvitationStatus.ACCEPTED, accepted_at=timezone.now()
        )
        record_audit(
            actor=None,
            action="staff.activated_without_invitation",
            object_type="User",
            object_id=user.pk,
        )
    return True


__all__ = [
    "accept_staff_invitation",
    "activate_invited_staff",
    "create_staff",
    "delete_invited",
    "get_member",
    "list_staff",
    "member_payload",
    "reactivate_staff",
    "reactivate_user",
    "resend_invitation",
    "resolve_staff_invitation",
    "suspend_staff",
    "suspend_user",
    "update_staff",
]
