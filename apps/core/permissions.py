"""Reusable authorization primitives.

Object-level scoping belongs in selectors; these classes only express coarse
role checks and simple ownership assertions. Scoped objects that a user may
not see must be returned as 404 by the selector, never 403.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.accounts.enums import Role


class IsAuthenticatedAndActive(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class HasRole(BasePermission):
    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.allowed_roles)


class IsPatient(HasRole):
    allowed_roles = (Role.PATIENT,)


class IsPhysician(HasRole):
    allowed_roles = (Role.PHYSICIAN,)


class IsStaff(HasRole):
    allowed_roles = (Role.STAFF, Role.ADMIN)


class IsAdmin(HasRole):
    allowed_roles = (Role.ADMIN,)


class IsClinicalStaff(HasRole):
    allowed_roles = (Role.PHYSICIAN, Role.STAFF, Role.ADMIN)


class IsEmailVerified(BasePermission):
    message = "Email verification is required for this action."

    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_email_verified)


class IsOwner(BasePermission):
    """Require ``request.user`` to own the fetched object.

    The owner field defaults to ``user`` and can be overridden per view.
    """

    owner_field = "user"

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        owner = getattr(obj, self.owner_field, None)
        return bool(owner == request.user)


class IsOwnerOrStaff(BasePermission):
    owner_field = "user"

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        if request.user.role in (Role.STAFF, Role.ADMIN):
            return True
        return bool(getattr(obj, self.owner_field, None) == request.user)


class ReadOnly(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return request.method in SAFE_METHODS


class CanAccessApiDocs(BasePermission):
    """Local development is open; production requires staff/admin."""

    def has_permission(self, request: Any, view: Any) -> bool:
        if settings.DEBUG:
            return True
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or getattr(user, "role", None) in (Role.STAFF, Role.ADMIN))
        )
