"""Signed prescription PDFs (Part 4, 4.8) and the generated unsigned fallback."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.assessments.models import Evaluation, Prescription
from apps.assessments.services import age_on
from apps.clinical.models import PrescriptionDocument, UploadStatus
from apps.clinical.pdf import simple_pdf
from apps.core.audit import record_audit
from apps.core.exceptions import FileTooLarge, InvalidStateTransition
from apps.core.storage import object_exists, object_size, presigned_upload_url, read_object_head

UPLOAD_TTL = 15 * 60


def _max_bytes() -> int:
    return int(settings.LAB_UPLOAD_MAX_BYTES)


def start_upload(*, user: User, data: dict[str, Any], request: Any) -> tuple[Any, dict[str, Any]]:
    evaluation = Evaluation.objects.filter(pk=data["evaluation_id"], physician=user).first()
    if evaluation is None:
        raise ValidationError({"evaluation_id": ["Claim this evaluation first."]})
    size = int(data["size_bytes"])
    if size < 1 or size > _max_bytes():
        raise FileTooLarge()
    key = f"prescriptions/{evaluation.pk}/signed-{uuid.uuid4().hex}.pdf"
    document = PrescriptionDocument.objects.create(
        evaluation=evaluation, uploaded_by=user, storage_key=key, size_bytes=size
    )
    url, headers = presigned_upload_url(key, content_type="application/pdf", expires_in=UPLOAD_TTL)
    record_audit(
        actor=user,
        action="prescription_document.created",
        object_type="PrescriptionDocument",
        object_id=document.pk,
        request=request,
    )
    return document, {
        "method": "PUT",
        "url": url,
        "headers": headers,
        "expires_at": timezone.now() + timedelta(seconds=UPLOAD_TTL),
    }


def _reject(document: PrescriptionDocument) -> None:
    document.status = UploadStatus.REJECTED
    document.scanned_at = timezone.now()
    document.save(update_fields=["status", "scanned_at", "updated_at"])


def confirm_upload(*, user: User, document_id: Any, request: Any) -> PrescriptionDocument:
    document = PrescriptionDocument.objects.filter(pk=document_id, uploaded_by=user).first()
    if document is None:
        raise NotFound()
    if document.status != UploadStatus.PENDING_UPLOAD:
        raise InvalidStateTransition()
    if not object_exists(document.storage_key):
        _reject(document)
        raise ValidationError({"upload": ["The file was not uploaded."]})
    size = object_size(document.storage_key)
    if size > _max_bytes():
        _reject(document)
        raise FileTooLarge()
    if size != document.size_bytes or not read_object_head(document.storage_key).startswith(
        b"%PDF-"
    ):
        _reject(document)
        raise ValidationError({"upload": ["The file is not a valid PDF."]})
    document.status = UploadStatus.SCANNING
    document.save(update_fields=["status", "updated_at"])
    record_audit(
        actor=user,
        action="prescription_document.confirmed",
        object_type="PrescriptionDocument",
        object_id=document.pk,
        request=request,
    )
    from apps.clinical.tasks import scan_prescription_document

    document_id_str = str(document.pk)
    transaction.on_commit(lambda: scan_prescription_document.delay(document_id_str))
    return document


def scan(document_id: Any) -> None:
    from apps.assessments.scanning import get_lab_scanner

    document = PrescriptionDocument.objects.filter(pk=document_id).first()
    if document is None or document.status != UploadStatus.SCANNING:
        return
    try:
        result = get_lab_scanner().scan(document.storage_key)
    except Exception:
        _reject(document)
        return
    document.status = UploadStatus.CLEAN if result.clean else UploadStatus.REJECTED
    document.scanned_at = timezone.now()
    document.save(update_fields=["status", "scanned_at", "updated_at"])


def generate_pdf(prescription_id: Any) -> str | None:
    """Unsigned prescription PDF built from the prescription data. [LEGAL REVIEW]"""

    prescription = (
        Prescription.objects.select_related(
            "evaluation__assessment__user",
            "evaluation__assessment__measurement",
            "issued_by__clinician_profile",
        )
        .filter(pk=prescription_id)
        .first()
    )
    if prescription is None or prescription.pdf_key:
        return None
    patient = prescription.evaluation.assessment.user
    measurement = getattr(prescription.evaluation.assessment, "measurement", None)
    physician = prescription.issued_by
    profile = getattr(physician, "clinician_profile", None) if physician else None
    lines = [
        "PRESCRIPTION (unsigned, generated from the electronic record)",
        "",
        f"Patient: {patient.get_full_name()}",
    ]
    if measurement is not None:
        lines.append(
            f"Date of birth: {measurement.date_of_birth.isoformat()} "
            f"(age {age_on(measurement.date_of_birth)})"
        )
    prescriber = profile.display_name if profile else ""
    if not prescriber and physician is not None:
        prescriber = physician.get_full_name()
    lines += [
        f"Prescriber: {prescriber}",
        f"License: {profile.license_number if profile and profile.license_number else '-'}",
        f"Start date: {prescription.start_date}   Valid until: {prescription.valid_until}",
        "",
    ]
    for index, treatment in enumerate(prescription.treatments.order_by("created_at"), start=1):
        lines += [
            f"{index}. {treatment.medication_name} - {treatment.dose_label}",
            f"   Route: {treatment.route}   Frequency: {treatment.frequency}"
            + (f" (day {treatment.day_of_week})" if treatment.day_of_week else ""),
            f"   Duration: {treatment.duration_weeks or '-'} weeks   "
            f"Refills: {treatment.refills_total or 0}",
            f"   Instructions: {treatment.instructions}",
            "",
        ]
    lines.append(f"Issued: {timezone.now().date().isoformat()}")
    key = f"prescriptions/{prescription.evaluation_id}/{uuid.uuid4().hex}.pdf"
    default_storage.save(key, ContentFile(simple_pdf(lines)))
    prescription.pdf_key = key
    prescription.save(update_fields=["pdf_key", "updated_at"])
    return key


__all__ = ["confirm_upload", "generate_pdf", "scan", "start_upload"]
