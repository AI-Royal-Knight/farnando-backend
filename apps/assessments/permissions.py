"""Role gates for Part 2 endpoints. Object scoping lives in selectors."""

from __future__ import annotations

from apps.accounts.enums import Role
from apps.core.permissions import HasRole


class IsPatientOrAdmin(HasRole):
    allowed_roles = (Role.PATIENT, Role.ADMIN)


class IsPatientPhysicianAdmin(HasRole):
    allowed_roles = (Role.PATIENT, Role.PHYSICIAN, Role.ADMIN)


class IsPatientStaffAdmin(HasRole):
    allowed_roles = (Role.PATIENT, Role.STAFF, Role.ADMIN)


class IsClinicalReader(HasRole):
    allowed_roles = (Role.PATIENT, Role.PHYSICIAN, Role.STAFF, Role.ADMIN)
