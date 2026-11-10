"""Read-only identity queries.

Keeping these here makes scoping explicit and gives future endpoints one place
to add select_related/prefetch_related instead of duplicating querysets.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from apps.accounts.models import ConsentRecord, User


def get_user_by_email(email: str) -> User | None:
    return User.objects.filter(email=email.strip().lower()).first()


def get_active_user_by_email(email: str) -> User | None:
    return User.objects.filter(email=email.strip().lower(), is_active=True).first()


def get_active_user_by_id(user_id: Any) -> User | None:
    return User.objects.filter(pk=user_id, is_active=True).first()


def get_consents_for_user(user: User) -> QuerySet[ConsentRecord]:
    return ConsentRecord.objects.filter(user=user).order_by("-accepted_at")
