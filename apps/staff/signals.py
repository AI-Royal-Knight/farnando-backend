"""Keep staff profiles in step with changes made outside the staff portal (e.g. Django admin)."""

from __future__ import annotations

from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import User


@receiver(post_save, sender=User, dispatch_uid="staff.activate_invited_staff")
def activate_invited_staff_on_save(sender: Any, instance: User, **kwargs: Any) -> None:
    if getattr(instance, "_accepting_staff_invitation", False):
        return  # accept_staff_invitation finishes the invitation itself
    from apps.staff.accounts import activate_invited_staff

    activate_invited_staff(instance)
