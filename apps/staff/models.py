"""Staff accounts, permissions and invitations (contract Part 5, sections 1 and 2)."""

from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class StaffType(models.TextChoices):
    NURSE = "NURSE", "Nurse"
    SUPPORT_AGENT = "SUPPORT_AGENT", "Support agent"
    OPERATIONS = "OPERATIONS", "Operations"


class AccountStatus(models.TextChoices):
    INVITED = "INVITED", "Invited"
    ACTIVE = "ACTIVE", "Active"
    SUSPENDED = "SUSPENDED", "Suspended"


class Permission(models.TextChoices):
    PATIENTS_READ = "PATIENTS_READ", "Read patients"
    PATIENTS_MANAGE = "PATIENTS_MANAGE", "Manage patients"
    CLINICAL_READ = "CLINICAL_READ", "Read clinical data"
    EVALUATIONS_READ = "EVALUATIONS_READ", "Read evaluations"
    EVALUATIONS_ASSIGN = "EVALUATIONS_ASSIGN", "Assign evaluations"
    EVALUATIONS_REQUEST_LABS = "EVALUATIONS_REQUEST_LABS", "Request labs"
    TICKETS_MANAGE = "TICKETS_MANAGE", "Manage tickets"
    COMPLAINTS_MANAGE = "COMPLAINTS_MANAGE", "Manage complaints"
    ORDERS_READ = "ORDERS_READ", "Read orders"
    ORDERS_MANAGE = "ORDERS_MANAGE", "Manage orders"
    ORDERS_REFUND = "ORDERS_REFUND", "Refund orders"
    SUBSCRIPTIONS_MANAGE = "SUBSCRIPTIONS_MANAGE", "Manage subscriptions"
    MESSAGES_SUPPORT = "MESSAGES_SUPPORT", "Support messages"
    MESSAGES_CLINICAL = "MESSAGES_CLINICAL", "Clinical messages"


# [OPEN] 13.4.1: admins can override per account.
DEFAULT_BUNDLES: dict[str, list[str]] = {
    StaffType.NURSE: [
        Permission.PATIENTS_READ,
        Permission.CLINICAL_READ,
        Permission.EVALUATIONS_READ,
        Permission.MESSAGES_CLINICAL,
        Permission.TICKETS_MANAGE,
    ],
    StaffType.SUPPORT_AGENT: [
        Permission.PATIENTS_READ,
        Permission.EVALUATIONS_READ,
        Permission.EVALUATIONS_ASSIGN,
        Permission.TICKETS_MANAGE,
        Permission.COMPLAINTS_MANAGE,
        Permission.ORDERS_READ,
        Permission.MESSAGES_SUPPORT,
    ],
    StaffType.OPERATIONS: [
        Permission.PATIENTS_READ,
        Permission.EVALUATIONS_READ,
        Permission.EVALUATIONS_ASSIGN,
        Permission.EVALUATIONS_REQUEST_LABS,
        Permission.ORDERS_READ,
        Permission.ORDERS_MANAGE,
        Permission.ORDERS_REFUND,
        Permission.SUBSCRIPTIONS_MANAGE,
    ],
}


class StaffProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile"
    )
    staff_type = models.CharField(max_length=16, choices=StaffType.choices)
    permissions = models.JSONField(default=list)
    status = models.CharField(
        max_length=12, choices=AccountStatus.choices, default=AccountStatus.ACTIVE, db_index=True
    )
    phone = models.CharField(max_length=16, blank=True, default="")
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.staff_type}"


class StaffInvitationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    EXPIRED = "EXPIRED", "Expired"
    REVOKED = "REVOKED", "Revoked"


class StaffInvitation(BaseModel):
    """Single-use set-password link. Only the SHA-256 of the token is stored."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_invitations",
    )
    email = models.EmailField()
    full_name = models.CharField(max_length=120)
    staff_type = models.CharField(max_length=16, choices=StaffType.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=12,
        choices=StaffInvitationStatus.choices,
        default=StaffInvitationStatus.PENDING,
    )
    expires_at = models.DateTimeField()
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.email}:{self.status}"
