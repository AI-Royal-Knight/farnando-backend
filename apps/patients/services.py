"""Patient profile, preferences, addresses and care-team rules."""

from __future__ import annotations

import re
import zoneinfo
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import User
from apps.assessments.models import Assessment, AssessmentMeasurement, Evaluation
from apps.core.exceptions import AddressInUse, Conflict, ReadOnlyField, UnknownField
from apps.patients.models import Address, PatientProfile, Ubigeo, UbigeoLevel

E164 = re.compile(r"^\+[1-9]\d{7,14}$")

PROFILE_READ_ONLY = ("date_of_birth", "sex", "email", "default_address_id", "created_at")
USER_READ_ONLY = ("id", "email", "role", "is_email_verified", "assessment_status", "created_at")
PREFERENCE_FIELDS = ("weight_unit", "height_unit", "language", "timezone", "notifications")
NOTIFICATION_FIELDS = ("email_messages", "email_orders", "email_reminders", "email_marketing")


def validate_e164(value: str, field: str) -> str:
    value = (value or "").strip()
    if not E164.match(value):
        raise ValidationError({field: ["Enter a phone number in E.164 format, e.g. +51987654321."]})
    return value


def valid_timezone(value: str) -> bool:
    return value in zoneinfo.available_timezones()


def get_or_create_profile(user: User) -> PatientProfile:
    existing = PatientProfile.objects.filter(user=user).select_related("assigned_nurse").first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            profile, _created = PatientProfile.objects.get_or_create(
                user=user, defaults={"timezone": settings.PORTAL_DEFAULT_TIMEZONE}
            )
    except IntegrityError:
        profile = PatientProfile.objects.get(user=user)
    return profile


def latest_measurement(user: User) -> AssessmentMeasurement | None:
    return (
        AssessmentMeasurement.objects.filter(assessment__user=user)
        .order_by("-assessment__created_at")
        .first()
    )


def _check_read_only(data: dict[str, Any]) -> None:
    for field in PROFILE_READ_ONLY:
        if field in data:
            raise ReadOnlyField(field)
    user_data = data.get("user")
    if isinstance(user_data, dict):
        for field in USER_READ_ONLY:
            if field in user_data:
                raise ReadOnlyField(f"user.{field}")


def update_profile(user: User, data: dict[str, Any]) -> PatientProfile:
    """Partial update. ``data`` is the raw request body (validated here)."""

    _check_read_only(data)
    allowed = {"phone", "preferences", "user"}
    for key in data:
        if key not in allowed:
            raise UnknownField(key)

    profile = get_or_create_profile(user)
    profile_fields: list[str] = []
    user_fields: list[str] = []

    if "phone" in data:
        phone = data["phone"]
        if phone is None or phone == "":
            profile.phone = ""
        elif isinstance(phone, str):
            profile.phone = validate_e164(phone, "phone")
        else:
            raise ValidationError({"phone": ["Must be a string or null."]})
        profile_fields.append("phone")

    if "user" in data:
        user_data = data["user"]
        if not isinstance(user_data, dict):
            raise ValidationError({"user": ["Must be an object."]})
        for key in user_data:
            if key != "full_name":
                raise UnknownField(f"user.{key}")
        if "full_name" in user_data:
            full_name = str(user_data["full_name"] or "").strip()
            if not (2 <= len(full_name) <= 120):
                raise ValidationError({"user.full_name": ["Must be 2 to 120 characters."]})
            user.full_name = full_name
            user_fields.append("full_name")

    if "preferences" in data:
        prefs = data["preferences"]
        if not isinstance(prefs, dict):
            raise ValidationError({"preferences": ["Must be an object."]})
        for key in prefs:
            if key not in PREFERENCE_FIELDS:
                raise UnknownField(f"preferences.{key}")
        choices = {
            "weight_unit": ("KG", "LB"),
            "height_unit": ("CM", "IN"),
            "language": ("es", "en"),
        }
        for key, values in choices.items():
            if key in prefs:
                if prefs[key] not in values:
                    raise ValidationError({f"preferences.{key}": [f"Must be one of {values}."]})
                setattr(profile, key, prefs[key])
                profile_fields.append(key)
        if "timezone" in prefs:
            if not isinstance(prefs["timezone"], str) or not valid_timezone(prefs["timezone"]):
                raise ValidationError({"preferences.timezone": ["Unknown IANA timezone."]})
            profile.timezone = prefs["timezone"]
            profile_fields.append("timezone")
        if "notifications" in prefs:
            notifications = prefs["notifications"]
            if not isinstance(notifications, dict):
                raise ValidationError({"preferences.notifications": ["Must be an object."]})
            for key, value in notifications.items():
                if key not in NOTIFICATION_FIELDS:
                    raise UnknownField(f"preferences.notifications.{key}")
                if not isinstance(value, bool):
                    raise ValidationError(
                        {f"preferences.notifications.{key}": ["Must be a boolean."]}
                    )
                if key == "email_marketing" and value and not profile.email_marketing:
                    profile.marketing_opt_in_at = timezone.now()
                    profile_fields.append("marketing_opt_in_at")
                setattr(profile, key, value)
                profile_fields.append(key)

    with transaction.atomic():
        if profile_fields:
            profile.save(update_fields=[*set(profile_fields), "updated_at"])
        if user_fields:
            user.save(update_fields=user_fields)
    return profile


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------


def resolve_district(code: str) -> Ubigeo:
    ubigeo = Ubigeo.objects.filter(code=code, level=UbigeoLevel.DISTRICT).first()
    if ubigeo is None:
        raise ValidationError({"ubigeo": ["Unknown district code."]})
    return ubigeo


def address_in_use(address: Address) -> bool:
    from apps.orders.models import (
        INACTIVE_ORDER_STATUSES,
        OPEN_SUBSCRIPTION_STATUSES,
        Order,
        Subscription,
    )

    open_order = (
        Order.objects.filter(shipping_address=address)
        .exclude(status__in=INACTIVE_ORDER_STATUSES)
        .exists()
    )
    if open_order:
        return True
    return Subscription.objects.filter(
        shipping_address=address, status__in=OPEN_SUBSCRIPTION_STATUSES
    ).exists()


def _set_default(user: User, address: Address) -> None:
    Address.objects.filter(user=user, is_default=True).exclude(pk=address.pk).update(
        is_default=False
    )
    if not address.is_default:
        address.is_default = True
        address.save(update_fields=["is_default", "updated_at"])
    profile = get_or_create_profile(user)
    profile.default_address = address
    profile.save(update_fields=["default_address", "updated_at"])


def create_address(user: User, data: dict[str, Any]) -> Address:
    with transaction.atomic():
        PatientProfile.objects.select_for_update().filter(pk=get_or_create_profile(user).pk).first()
        if Address.objects.filter(user=user).count() >= settings.ADDRESS_MAX_PER_PATIENT:
            raise Conflict(
                f"You can save up to {settings.ADDRESS_MAX_PER_PATIENT} addresses.",
            )
        make_default = bool(data.pop("is_default", False))
        is_first = not Address.objects.filter(user=user).exists()
        address = Address.objects.create(user=user, **data)
        if make_default or is_first:
            _set_default(user, address)
    return address


def update_address(user: User, address: Address, data: dict[str, Any]) -> Address:
    with transaction.atomic():
        make_default = data.pop("is_default", None)
        for key, value in data.items():
            setattr(address, key, value)
        if data:
            address.save()
        if make_default is True:
            _set_default(user, address)
    return address


def delete_address(user: User, address: Address) -> None:
    if address_in_use(address):
        raise AddressInUse()
    with transaction.atomic():
        was_default = address.is_default
        address.delete()
        if was_default:
            replacement = Address.objects.filter(user=user).order_by("-created_at").first()
            if replacement is not None:
                _set_default(user, replacement)
            else:
                profile = get_or_create_profile(user)
                profile.default_address = None
                profile.save(update_fields=["default_address", "updated_at"])


def make_default(user: User, address: Address) -> None:
    with transaction.atomic():
        _set_default(user, address)


# ---------------------------------------------------------------------------
# Care team
# ---------------------------------------------------------------------------


def care_team_users(user: User) -> list[User]:
    """Physicians on evaluations/treatments plus the assigned nurse (stable order)."""

    from apps.treatments.models import Treatment

    members: dict[Any, User] = {}
    evaluations = (
        Evaluation.objects.filter(assessment__user=user, physician__isnull=False)
        .select_related("physician")
        .order_by("-submitted_at")
    )
    for evaluation in evaluations:
        physician = evaluation.physician
        if physician is not None and physician.is_active:
            members.setdefault(physician.pk, physician)
    treatments = (
        Treatment.objects.filter(patient=user)
        .select_related("prescribing_physician")
        .order_by("-started_at")
    )
    for treatment in treatments:
        physician = treatment.prescribing_physician
        if physician.is_active:
            members.setdefault(physician.pk, physician)
    profile = get_or_create_profile(user)
    assigned = profile.assigned_physician
    if assigned is not None and assigned.is_active:
        members = {assigned.pk: assigned, **members}
    nurse = profile.assigned_nurse
    if nurse is not None and nurse.is_active:
        members.setdefault(nurse.pk, nurse)
    return list(members.values())


def is_in_care_team(user: User, participant_id: Any) -> User | None:
    for member in care_team_users(user):
        if str(member.pk) == str(participant_id):
            return member
    return None


def relevant_assessment(user: User) -> Assessment | None:
    queryset = (
        Assessment.objects.filter(user=user).select_related("program").order_by("-created_at")
    )
    return queryset.exclude(status="CANCELLED").first() or queryset.first()


__all__ = [
    "address_in_use",
    "care_team_users",
    "create_address",
    "delete_address",
    "get_or_create_profile",
    "is_in_care_team",
    "latest_measurement",
    "make_default",
    "relevant_assessment",
    "resolve_district",
    "update_address",
    "update_profile",
    "valid_timezone",
    "validate_e164",
]
