from __future__ import annotations

from django.apps import AppConfig


class StaffConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.staff"
    label = "staff"
    verbose_name = "Staff and admin portals"

    def ready(self) -> None:
        from apps.staff import signals  # noqa: F401
