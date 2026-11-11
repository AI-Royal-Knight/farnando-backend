"""Clinician lookups shared by consultations, treatments, messaging and patients."""

from __future__ import annotations

from typing import Any

from apps.accounts.enums import Role
from apps.consultations.models import CareRole, ClinicianProfile


def profile_for(user: Any) -> ClinicianProfile | None:
    try:
        return user.clinician_profile  # type: ignore[no-any-return]
    except ClinicianProfile.DoesNotExist:
        return None


def care_role_for(user: Any) -> str | None:
    """PHYSICIAN or NURSE, or ``None`` if the user is not a clinician."""

    profile = profile_for(user)
    if profile is not None:
        return profile.care_role
    if getattr(user, "role", None) == Role.PHYSICIAN:
        return CareRole.PHYSICIAN
    return None


def physician_summary(user: Any) -> dict[str, Any]:
    """``PhysicianSummary`` (contract Part 3 section 1)."""

    profile = profile_for(user)
    return {
        "id": str(user.pk),
        "name": (profile.display_name if profile and profile.display_name else None)
        or user.get_full_name(),
        "headline": profile.headline if profile else "",
        "avatar_url": avatar_url(profile),
    }


def avatar_url(profile: ClinicianProfile | None) -> str | None:
    from apps.clinical.onboarding import avatar_url_for

    return avatar_url_for(profile)


def care_member_summary(user: Any) -> dict[str, Any]:
    summary = physician_summary(user)
    summary["role"] = care_role_for(user) or CareRole.PHYSICIAN
    return summary


__all__ = ["care_member_summary", "care_role_for", "physician_summary", "profile_for"]
