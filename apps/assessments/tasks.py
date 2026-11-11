"""Celery tasks for lab scanning and evaluation events."""

from __future__ import annotations

import structlog
from django.utils import timezone

from apps.assessments.enums import LabRejectionReason, LabUploadStatus
from apps.assessments.models import LabUpload
from apps.assessments.scanning import get_lab_scanner
from celery import shared_task

logger = structlog.get_logger(__name__)


@shared_task(  # type: ignore[untyped-decorator]
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    time_limit=120,
    soft_time_limit=90,
)
def scan_lab_upload(self: object, upload_id: str) -> None:
    _ = self
    upload = LabUpload.objects.filter(pk=upload_id).first()
    if upload is None or upload.status != LabUploadStatus.SCANNING:
        return
    try:
        result = get_lab_scanner().scan(upload.storage_key)
    except Exception as exc:
        logger.warning("lab_scan_failed", upload_id=str(upload.pk), error=type(exc).__name__)
        upload.status = LabUploadStatus.REJECTED
        upload.rejection_reason = LabRejectionReason.UNREADABLE
        upload.scanned_at = timezone.now()
        upload.save(update_fields=["status", "rejection_reason", "scanned_at", "updated_at"])
        return
    upload.status = LabUploadStatus.CLEAN if result.clean else LabUploadStatus.REJECTED
    upload.rejection_reason = "" if result.clean else result.reason
    upload.scanned_at = timezone.now()
    upload.save(update_fields=["status", "rejection_reason", "scanned_at", "updated_at"])
    logger.info("lab_scan_completed", upload_id=str(upload.pk), status=upload.status)
