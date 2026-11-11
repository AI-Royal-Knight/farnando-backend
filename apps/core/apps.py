"""Core app configuration."""

from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Core"

    def ready(self) -> None:
        from apps.core import schema_extensions  # noqa: F401 - register spectacular extension
        from apps.core.logging import configure_logging

        configure_logging()
        _render_datetimes_in_utc()


def _render_datetimes_in_utc() -> None:
    """The contract says every instant is UTC with a trailing ``Z``.

    ``REST_FRAMEWORK["DATETIME_FORMAT"]`` appends a literal ``Z``, but DRF
    converts values to the *current* timezone (``America/Lima``) before
    formatting, which produced local times labelled as UTC. Pin serializer
    rendering (and parsing of naive input) to UTC. Business-date logic keeps
    using ``timezone.localdate()`` in Lima.
    """

    from datetime import UTC
    from datetime import tzinfo as TzInfo

    from rest_framework import serializers

    def default_timezone(self: serializers.DateTimeField) -> TzInfo:
        return UTC

    serializers.DateTimeField.default_timezone = default_timezone  # type: ignore[assignment]
