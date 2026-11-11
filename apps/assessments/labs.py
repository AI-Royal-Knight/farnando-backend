"""Lab-upload pre-signed flow, scanning gate and downloads."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.assessments.enums import (
    AssessmentStatus,
    EvaluationStatus,
    LabRejectionReason,
    LabsStatus,
    LabUploadStatus,
)
from apps.assessments.models import Assessment, Evaluation, LabUpload
from apps.assessments.services import EvaluationService
from apps.core.audit import record_audit
from apps.core.exceptions import (
    FileTooLarge,
    InvalidStateTransition,
    NoLabFiles,
    UploadLimitReached,
)
from apps.core.storage import (
    delete_object,
    object_exists,
    object_size,
    presigned_upload_url,
    presigned_url,
    read_object_head,
)

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}
FORBIDDEN_DELETE_STATUSES = {
    EvaluationStatus.IN_REVIEW,
    EvaluationStatus.APPROVED,
    EvaluationStatus.NOT_SUITABLE,
}


def _max_bytes() -> int:
    return int(getattr(settings, "LAB_UPLOAD_MAX_BYTES", 10 * 1024 * 1024))


def _upload_ttl() -> int:
    return int(getattr(settings, "LAB_UPLOAD_URL_TTL_SECONDS", 900))


def _download_ttl() -> int:
    return int(getattr(settings, "LAB_DOWNLOAD_URL_TTL_SECONDS", 300))


def sniff_content_type(head: bytes) -> str | None:
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _reject(upload: LabUpload, reason: str) -> None:
    upload.status = LabUploadStatus.REJECTED
    upload.rejection_reason = reason
    upload.scanned_at = timezone.now()
    upload.save(update_fields=["status", "rejection_reason", "scanned_at", "updated_at"])


def start_lab_upload(
    *, assessment: Assessment, data: dict[str, Any], request: Any
) -> tuple[LabUpload, dict[str, Any]]:
    if assessment.status != AssessmentStatus.SUBMITTED:
        raise InvalidStateTransition()
    evaluation = Evaluation.objects.filter(assessment=assessment).first()
    if evaluation is None:
        raise InvalidStateTransition()
    if evaluation.status not in {
        EvaluationStatus.SUBMITTED,
        EvaluationStatus.AWAITING_PAYMENT,
        EvaluationStatus.IN_REVIEW,
        EvaluationStatus.PENDING_LABS,
    }:
        raise InvalidStateTransition()

    active_uploads = assessment.lab_uploads.filter(is_deleted=False).exclude(
        status=LabUploadStatus.REJECTED
    )
    if active_uploads.count() >= 5:
        raise UploadLimitReached()

    file_name = str(data["file_name"]).strip()
    if not (1 <= len(file_name) <= 150):
        raise ValidationError({"file_name": ["File name must be 1 to 150 characters."]})
    content_type = str(data["content_type"]).lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError({"content_type": ["Unsupported file type."]})
    size_bytes = int(data["size_bytes"])
    if size_bytes < 1 or size_bytes > _max_bytes():
        raise FileTooLarge()

    suffix = Path(file_name).suffix.lower()[:10]
    storage_key = f"labs/{assessment.pk}/{uuid.uuid4().hex}{suffix}"
    upload = LabUpload.objects.create(
        assessment=assessment,
        uploaded_by=assessment.user,
        file_name=file_name,
        storage_key=storage_key,
        content_type=content_type,
        size_bytes=size_bytes,
        status=LabUploadStatus.PENDING_UPLOAD,
    )
    url, headers = presigned_upload_url(
        storage_key, content_type=content_type, expires_in=_upload_ttl()
    )
    record_audit(
        actor=assessment.user,
        action="lab_upload.created",
        object_type="LabUpload",
        object_id=upload.pk,
        request=request,
    )
    return upload, {
        "method": "PUT",
        "url": url,
        "headers": headers,
        "expires_at": timezone.now() + timedelta(seconds=_upload_ttl()),
    }


def confirm_lab_upload(*, upload: LabUpload, request: Any) -> LabUpload:
    if upload.status != LabUploadStatus.PENDING_UPLOAD:
        raise InvalidStateTransition()

    if not object_exists(upload.storage_key):
        _reject(upload, LabRejectionReason.UPLOAD_MISSING)
        raise ValidationError({"rejection_reason": [LabRejectionReason.UPLOAD_MISSING]})

    actual_size = object_size(upload.storage_key)
    if actual_size <= 0:
        _reject(upload, LabRejectionReason.UPLOAD_MISSING)
        raise ValidationError({"rejection_reason": [LabRejectionReason.UPLOAD_MISSING]})
    if actual_size > _max_bytes():
        _reject(upload, LabRejectionReason.FILE_TOO_LARGE)
        raise FileTooLarge()
    if actual_size != upload.size_bytes:
        _reject(upload, LabRejectionReason.UNREADABLE)
        raise ValidationError({"rejection_reason": [LabRejectionReason.UNREADABLE]})

    try:
        head = read_object_head(upload.storage_key)
    except Exception as exc:
        _reject(upload, LabRejectionReason.UNREADABLE)
        raise ValidationError({"rejection_reason": [LabRejectionReason.UNREADABLE]}) from exc
    sniffed = sniff_content_type(head)
    if sniffed is None or sniffed not in ALLOWED_CONTENT_TYPES:
        _reject(upload, LabRejectionReason.INVALID_FILE_TYPE)
        raise ValidationError({"rejection_reason": [LabRejectionReason.INVALID_FILE_TYPE]})

    upload.content_type = sniffed
    upload.status = LabUploadStatus.SCANNING
    upload.save(update_fields=["content_type", "status", "updated_at"])
    record_audit(
        actor=upload.uploaded_by,
        action="lab_upload.confirmed",
        object_type="LabUpload",
        object_id=upload.pk,
        request=request,
    )
    from django.db import transaction

    from apps.assessments.tasks import scan_lab_upload

    transaction.on_commit(lambda: scan_lab_upload.delay(str(upload.pk)))
    return upload


def delete_lab_upload(*, upload: LabUpload, request: Any) -> None:
    evaluation = Evaluation.objects.filter(assessment=upload.assessment).first()
    if evaluation is not None and evaluation.status in FORBIDDEN_DELETE_STATUSES:
        raise InvalidStateTransition()
    delete_object(upload.storage_key)
    upload.is_deleted = True
    upload.deleted_at = timezone.now()
    upload.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    record_audit(
        actor=upload.uploaded_by,
        action="lab_upload.deleted",
        object_type="LabUpload",
        object_id=upload.pk,
        request=request,
    )


def submit_labs(*, assessment: Assessment, request: Any) -> dict[str, Any]:
    evaluation = Evaluation.objects.filter(assessment=assessment).first()
    if evaluation is None:
        raise InvalidStateTransition()
    available = assessment.lab_uploads.filter(
        is_deleted=False, status__in=[LabUploadStatus.SCANNING, LabUploadStatus.CLEAN]
    )
    if not available.exists():
        raise NoLabFiles()
    assessment.labs_status = LabsStatus.SUBMITTED
    assessment.save(update_fields=["labs_status", "updated_at"])
    if evaluation.status == EvaluationStatus.PENDING_LABS:
        EvaluationService.transition(
            evaluation,
            EvaluationStatus.SUBMITTED,
            actor=None,
            reason="Patient submitted lab results",
        )
    if evaluation.physician_id is not None:
        from apps.notifications.models import NotificationType
        from apps.notifications.services import notify

        notify(
            evaluation.physician,
            NotificationType.EVALUATION_LABS_UPLOADED,
            target_kind="clinical_evaluation",
            target_id=evaluation.pk,
            email=False,
        )
    record_audit(
        actor=assessment.user,
        action="labs.submitted",
        object_type="Assessment",
        object_id=assessment.pk,
        request=request,
    )
    return {
        "labs_status": LabsStatus.SUBMITTED,
        "evaluation_status": evaluation.status,
    }


def skip_labs(*, assessment: Assessment, request: Any) -> dict[str, Any]:
    if assessment.status != AssessmentStatus.SUBMITTED:
        raise InvalidStateTransition()
    if assessment.labs_status != LabsStatus.NOT_STARTED:
        raise InvalidStateTransition()
    assessment.labs_status = LabsStatus.DEFERRED
    assessment.save(update_fields=["labs_status", "updated_at"])
    record_audit(
        actor=assessment.user,
        action="labs.deferred",
        object_type="Assessment",
        object_id=assessment.pk,
        request=request,
    )
    return {"labs_status": LabsStatus.DEFERRED}


def download_lab_upload(*, upload: LabUpload, request: Any) -> dict[str, Any]:
    if upload.status != LabUploadStatus.CLEAN:
        raise InvalidStateTransition()
    if not object_exists(upload.storage_key):
        raise NotFound()
    url = presigned_url(upload.storage_key, expires_in=_download_ttl())
    record_audit(
        actor=upload.uploaded_by if request.user == upload.uploaded_by else request.user,
        action="lab_upload.downloaded",
        object_type="LabUpload",
        object_id=upload.pk,
        request=request,
    )
    return {"url": url, "expires_at": timezone.now() + timedelta(seconds=_download_ttl())}


def download_prescription(*, evaluation: Evaluation, request: Any) -> dict[str, Any]:
    if evaluation.status != EvaluationStatus.APPROVED:
        raise InvalidStateTransition()
    prescription = getattr(evaluation, "prescription", None)
    if prescription is None or not prescription.pdf_key or not prescription.can_download:
        raise InvalidStateTransition()
    url = presigned_url(prescription.pdf_key, expires_in=_download_ttl())
    record_audit(
        actor=request.user,
        action="prescription.downloaded",
        object_type="Prescription",
        object_id=prescription.pk,
        request=request,
    )
    return {"url": url, "expires_at": timezone.now() + timedelta(seconds=_download_ttl())}


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "confirm_lab_upload",
    "delete_lab_upload",
    "download_lab_upload",
    "download_prescription",
    "skip_labs",
    "sniff_content_type",
    "start_lab_upload",
    "submit_labs",
]
