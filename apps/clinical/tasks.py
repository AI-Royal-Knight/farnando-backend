"""Clinician-side background work. Arguments are ids only; emails carry no PHI."""

from __future__ import annotations

from typing import Any

import structlog
from django.conf import settings

from apps.core.email import get_email_service
from celery import shared_task

logger = structlog.get_logger(__name__)

STAFF_ALERTS = {
    "physician_pending_verification": (
        "Physician credentials to verify",
        "A new physician account is waiting for credential verification.",
    ),
    "evaluation_rejected": (
        "Evaluation declined",
        "A physician declined an evaluation. Review the fee refund rule.",
    ),
    "physician_suspended": (
        "Consultations need a new physician",
        "A physician was suspended. Confirmed consultations are flagged for staff.",
    ),
    "privacy_request_received": (
        "Privacy request received",
        "A patient filed a privacy request. Open the admin privacy queue.",
    ),
    "refund_task_created": (
        "Refund to review",
        "A refund task was created. Open the support tickets to process it.",
    ),
    "subscription_needs_review": (
        "Subscription needs review",
        "A treatment was discontinued and a subscription needs staff review.",
    ),
}


@shared_task(acks_late=True, time_limit=60)  # type: ignore[untyped-decorator]
def send_staff_alert(kind: str) -> int:
    subject, message = STAFF_ALERTS[kind]
    sent = 0
    for address in settings.SUPPORT_NOTIFICATION_EMAILS:
        try:
            get_email_service().send(
                to=address, subject=subject, template="staff_alert", context={"message": message}
            )
            sent += 1
        except Exception as exc:
            logger.warning("staff_alert_failed", kind=kind, error=type(exc).__name__)
    return sent


@shared_task(acks_late=True, time_limit=120)  # type: ignore[untyped-decorator]
def generate_prescription_pdf(prescription_id: str) -> str | None:
    from apps.clinical.documents import generate_pdf

    return generate_pdf(prescription_id)


@shared_task(acks_late=True, time_limit=120)  # type: ignore[untyped-decorator]
def scan_prescription_document(document_id: str) -> None:
    from apps.clinical.documents import scan

    scan(document_id)


@shared_task(acks_late=True, time_limit=600)  # type: ignore[untyped-decorator]
def run_clinical_jobs() -> dict[str, Any]:
    from apps.clinical.evaluations import auto_release_stale_claims
    from apps.clinical.treatments import apply_due_versions

    result = {
        "released_claims": auto_release_stale_claims(),
        "applied_versions": apply_due_versions(),
    }
    logger.info("clinical_jobs", **result)
    return result
