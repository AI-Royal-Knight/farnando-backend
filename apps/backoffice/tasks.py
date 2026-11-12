"""Background jobs for the admin back office."""

from __future__ import annotations

from apps.backoffice import reports
from celery import shared_task


@shared_task(acks_late=True, time_limit=300, soft_time_limit=280)  # type: ignore[untyped-decorator]
def build_report_export(export_id: str) -> None:
    reports.build_export(export_id)
