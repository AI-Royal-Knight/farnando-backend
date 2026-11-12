"""Role and permission checks for the staff and admin portals (Part 5, sections 1 and 12)."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission

from apps.accounts.enums import Role
from apps.core.exceptions import ApplicationError
from apps.staff.models import AccountStatus, Permission, StaffProfile

ALL_PERMISSIONS = frozenset(Permission.values)


class MissingPermission(ApplicationError):
    status_code = 403
    default_detail = "You do not have permission to perform this action."
    default_code = "permission_denied"


def staff_profile(user: Any) -> StaffProfile | None:
    try:
        return user.staff_profile  # type: ignore[no-any-return]
    except (StaffProfile.DoesNotExist, AttributeError):
        return None


def permissions_of(user: Any) -> frozenset[str]:
    if not getattr(user, "is_authenticated", False):
        return frozenset()
    if user.role == Role.ADMIN:
        return ALL_PERMISSIONS
    if user.role != Role.STAFF:
        return frozenset()
    profile = staff_profile(user)
    if profile is None or profile.status != AccountStatus.ACTIVE:
        return frozenset()
    return frozenset(str(item) for item in profile.permissions) & ALL_PERMISSIONS


def has_perm(user: Any, permission: str) -> bool:
    return permission in permissions_of(user)


def has_clinical_access(user: Any) -> bool:
    perms = permissions_of(user)
    return Permission.CLINICAL_READ in perms or Permission.MESSAGES_CLINICAL in perms


def require_perm(user: Any, permission: str) -> None:
    if not has_perm(user, permission):
        raise MissingPermission(details={"required": permission})


def is_staff_or_admin(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.role in (Role.STAFF, Role.ADMIN))


class IsStaffOrAdmin(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return is_staff_or_admin(request.user)


class IsAdminRole(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.ADMIN)


class StaffPermission(BasePermission):
    """Role check (403, no details) then permission check (403 with ``required``).

    Views set ``required_permission`` (all methods) or
    ``required_permissions = {"GET": ..., "POST": ...}``.
    """

    def has_permission(self, request: Any, view: Any) -> bool:
        if not is_staff_or_admin(request.user):
            return False
        mapping: dict[str, str] = getattr(view, "required_permissions", {}) or {}
        required = mapping.get(request.method) or getattr(view, "required_permission", None)
        if required:
            require_perm(request.user, required)
        return True


__all__ = [
    "ALL_PERMISSIONS",
    "IsAdminRole",
    "IsStaffOrAdmin",
    "MissingPermission",
    "StaffPermission",
    "has_clinical_access",
    "has_perm",
    "is_staff_or_admin",
    "permissions_of",
    "require_perm",
    "staff_profile",
]
