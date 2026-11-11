"""Clinician gates. Object scoping (care relationship, assignee) lives in services."""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from apps.accounts.enums import Role
from apps.consultations.models import (
    CareRole,
    ClinicianProfile,
    PhysicianVerificationStatus,
)
from apps.core.exceptions import CredentialsNotVerified


def physician_profile(user: Any) -> ClinicianProfile:
    """The physician's profile, created lazily as PENDING when missing."""

    profile = ClinicianProfile.objects.filter(user=user).select_related("specialty_ref").first()
    if profile is not None:
        return profile
    try:
        with transaction.atomic():
            return ClinicianProfile.objects.create(
                user=user,
                care_role=CareRole.PHYSICIAN,
                display_name=user.get_full_name(),
                verification_status=PhysicianVerificationStatus.PENDING,
            )
    except IntegrityError:
        return ClinicianProfile.objects.get(user=user)


def is_verified_physician(user: Any) -> bool:
    if getattr(user, "role", None) != Role.PHYSICIAN:
        return False
    return physician_profile(user).verification_status == PhysicianVerificationStatus.VERIFIED


def require_verified(user: Any) -> ClinicianProfile:
    if getattr(user, "role", None) != Role.PHYSICIAN:
        raise PermissionDenied()
    profile = physician_profile(user)
    if profile.verification_status != PhysicianVerificationStatus.VERIFIED:
        raise CredentialsNotVerified(details={"verification_status": profile.verification_status})
    return profile


class IsPhysician(BasePermission):
    """Any physician, whatever the verification status."""

    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.PHYSICIAN)


class IsVerifiedPhysician(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        require_verified(user)
        return True


class IsVerifiedPhysicianOrAdmin(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role == Role.ADMIN:
            return True
        require_verified(user)
        return True


__all__ = [
    "IsPhysician",
    "IsVerifiedPhysician",
    "IsVerifiedPhysicianOrAdmin",
    "is_verified_physician",
    "physician_profile",
    "require_verified",
]
