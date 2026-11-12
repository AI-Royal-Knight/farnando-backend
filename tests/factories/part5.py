"""Helpers for the Part 5 staff and admin tests."""

from __future__ import annotations

import re
from collections.abc import Iterable

from django.core import mail

from apps.accounts.models import User
from apps.staff.models import DEFAULT_BUNDLES, AccountStatus, StaffProfile
from tests.factories.users import StaffFactory


def make_staff(
    staff_type: str = "SUPPORT_AGENT",
    permissions: Iterable[str] | None = None,
    *,
    name: str = "Sofia Lopez",
) -> User:
    user = StaffFactory(full_name=name)
    StaffProfile.objects.create(
        user=user,
        staff_type=staff_type,
        permissions=sorted(permissions if permissions is not None else DEFAULT_BUNDLES[staff_type]),
        status=AccountStatus.ACTIVE,
    )
    return user


def token_from_outbox(path: str) -> str:
    for message in reversed(mail.outbox):
        match = re.search(rf"/{path}/invitations/([A-Za-z0-9_\-]+)", message.body)
        if match:
            return match.group(1)
    raise AssertionError("No invitation link was emailed.")
