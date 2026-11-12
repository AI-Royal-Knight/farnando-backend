"""Invitation emails. The raw token is created here, so it never travels in task arguments."""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.tasks import _TASK_OPTIONS, _send
from celery import shared_task


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_staff_invitation(self: object, invitation_id: str) -> None:
    from apps.clinical.onboarding import hash_token
    from apps.staff.models import StaffInvitation, StaffInvitationStatus

    _ = self
    invitation = StaffInvitation.objects.filter(
        pk=invitation_id, status=StaffInvitationStatus.PENDING
    ).first()
    if invitation is None:
        return
    token = secrets.token_urlsafe(32)
    invitation.token_hash = hash_token(token)
    invitation.expires_at = timezone.now() + timedelta(days=settings.STAFF_INVITATION_TTL_DAYS)
    invitation.save(update_fields=["token_hash", "expires_at", "updated_at"])
    _send(
        to=invitation.email,
        subject="Your Adelgaceya staff invitation",
        template="staff_invitation",
        context={
            "full_name": invitation.full_name,
            "link": f"{settings.FRONTEND_URL}/staff/invitations/{token}",
            "expires_at": timezone.localtime(invitation.expires_at).strftime("%Y-%m-%d %H:%M"),
        },
    )


@shared_task(**_TASK_OPTIONS)  # type: ignore[untyped-decorator]
def send_physician_invitation(self: object, invitation_id: str) -> None:
    from apps.clinical.models import InvitationStatus, PhysicianInvitation
    from apps.clinical.onboarding import hash_token

    _ = self
    invitation = PhysicianInvitation.objects.filter(
        pk=invitation_id, status=InvitationStatus.PENDING
    ).first()
    if invitation is None:
        return
    token = secrets.token_urlsafe(32)
    invitation.token_hash = hash_token(token)
    invitation.expires_at = timezone.now() + timedelta(days=settings.PHYSICIAN_INVITATION_TTL_DAYS)
    invitation.save(update_fields=["token_hash", "expires_at", "updated_at"])
    _send(
        to=invitation.email,
        subject="Your Adelgaceya physician invitation",
        template="physician_invitation",
        context={
            "link": f"{settings.FRONTEND_URL}/physicians/invitations/{token}",
            "expires_at": timezone.localtime(invitation.expires_at).strftime("%Y-%m-%d %H:%M"),
        },
    )
