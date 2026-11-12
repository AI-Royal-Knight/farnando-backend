"""Reference data CRUD (Part 5, 9.2) and versioned legal documents (9.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, models, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import LegalDocument, User
from apps.catalog.models import FAQ, ComplaintCategory, LabPanel, Testimonial, TicketCategory
from apps.clinical.models import Specialty
from apps.consultations.models import ConsultationType
from apps.core.audit import record_audit
from apps.core.exceptions import InvalidStateTransition

LIMA = "America/Lima"


@dataclass(frozen=True)
class Resource:
    model: type[models.Model]
    fields: tuple[str, ...]
    search: tuple[str, ...]
    active_field: str | None
    in_use: Callable[[Any], bool]


def _never(_: Any) -> bool:
    return False


RESOURCES: dict[str, Resource] = {
    "faqs": Resource(
        FAQ,
        ("question", "answer_markdown", "position", "is_published"),
        ("question",),
        "is_published",
        _never,
    ),
    "testimonials": Resource(
        Testimonial,
        ("author_name", "rating", "body", "patient_consent_recorded", "is_published"),
        ("author_name", "body"),
        "is_published",
        _never,
    ),
    "complaint-categories": Resource(
        ComplaintCategory,
        ("code", "label", "position", "is_active"),
        ("code", "label"),
        "is_active",
        lambda obj: obj.complaintbookentry_set.exists(),
    ),
    "support-ticket-categories": Resource(
        TicketCategory,
        ("code", "label", "requires_clinical_staff", "position", "is_active"),
        ("code", "label"),
        "is_active",
        lambda obj: obj.tickets.exists(),
    ),
    "specialties": Resource(
        Specialty,
        ("name", "requires_registration_number", "is_active"),
        ("name",),
        "is_active",
        lambda obj: obj.clinicians.exists() or obj.programs.exists(),
    ),
    "consultation-types": Resource(
        ConsultationType,
        ("code", "name", "description", "duration_minutes", "is_initial", "is_active"),
        ("code", "name"),
        "is_active",
        lambda obj: obj.consultations.exists(),
    ),
    "lab-panels": Resource(
        LabPanel,
        ("code", "name", "description", "is_active"),
        ("code", "name"),
        "is_active",
        _never,
    ),
}

# Hard delete for items that nothing references (deviation 125).
ALWAYS_REMOVE = {"faqs", "testimonials", "lab-panels"}


def resource(name: str) -> Resource:
    try:
        return RESOURCES[name]
    except KeyError as exc:  # pragma: no cover - URL patterns restrict the names
        raise NotFound() from exc


def payload(name: str, obj: Any) -> dict[str, Any]:
    spec = resource(name)
    body: dict[str, Any] = {"id": obj.pk}
    for field in spec.fields:
        body[field] = getattr(obj, field)
    if name == "testimonials":
        body["published_at"] = obj.published_at if obj.is_published else None
    return body


def filtered(name: str, params: Any) -> QuerySet[Any]:
    spec = resource(name)
    queryset = spec.model._default_manager.all()
    term = params.get("search")
    if term:
        condition = Q()
        for field in spec.search:
            condition |= Q(**{f"{field}__icontains": term})
        queryset = queryset.filter(condition)
    raw_active = params.get("is_active")
    if raw_active in ("true", "false") and spec.active_field:
        queryset = queryset.filter(**{spec.active_field: raw_active == "true"})
    return queryset


def get(name: str, pk: Any) -> Any:
    obj = resource(name).model._default_manager.filter(pk=pk).first()
    if obj is None:
        raise NotFound()
    return obj


def _validate(name: str, obj: Any, data: dict[str, Any]) -> None:
    if name == "testimonials":
        published = data.get("is_published", obj.is_published)
        consent = data.get("patient_consent_recorded", obj.patient_consent_recorded)
        if published and not consent:
            raise ValidationError(
                {"patient_consent_recorded": ["Consent must be recorded before publishing."]}
            )
    if name == "consultation-types" and data.get("is_initial"):
        others = ConsultationType.objects.filter(is_initial=True).exclude(pk=obj.pk)
        if others.exists():
            raise ValidationError({"is_initial": ["Only one initial consultation type."]})


def save(name: str, *, obj: Any | None, data: dict[str, Any], actor: User, request: Any) -> Any:
    spec = resource(name)
    creating = obj is None
    target = obj if obj is not None else spec.model()
    _validate(name, target, data)
    for field, value in data.items():
        if field in spec.fields:
            setattr(target, field, value)
    if name == "testimonials":
        was_published = bool(getattr(obj, "is_published", False))
        if creating or (getattr(target, "is_published", False) and not was_published):
            setattr(target, "published_at", timezone.now())  # noqa: B010
    try:
        with transaction.atomic():
            target.save()
    except IntegrityError as exc:
        raise ValidationError({"code": ["Already used."]}) from exc
    record_audit(
        actor=actor,
        action=f"reference.{'created' if creating else 'updated'}",
        object_type=spec.model.__name__,
        object_id=target.pk,
        request=request,
        changed_fields=sorted(data),
    )
    return target


def delete(name: str, *, obj: Any, actor: User, request: Any) -> bool:
    """Delete or deactivate. Returns True when the row was removed."""

    spec = resource(name)
    removed = name in ALWAYS_REMOVE or not spec.in_use(obj)
    pk = obj.pk
    if removed:
        obj.delete()
    else:
        assert spec.active_field is not None
        setattr(obj, spec.active_field, False)
        obj.save(update_fields=[spec.active_field])
    record_audit(
        actor=actor,
        action="reference.deleted" if removed else "reference.deactivated",
        object_type=spec.model.__name__,
        object_id=pk,
        request=request,
    )
    return removed


# ---------------------------------------------------------------------------
# Legal documents (9.3)
# ---------------------------------------------------------------------------

DRAFT = "DRAFT"
PUBLISHED = "PUBLISHED"


def legal_payload(document: LegalDocument) -> dict[str, Any]:
    return {
        "id": document.pk,
        "document_type": document.document_type,
        "title": document.title,
        "sections": document.sections,
        "status": document.status,
        "version": document.version if document.status == PUBLISHED else None,
        "is_current": document.is_current,
        "effective_at": document.effective_at,
        "published_at": document.published_at if document.status == PUBLISHED else None,
    }


def legal_documents(params: Any) -> QuerySet[LegalDocument]:
    queryset = LegalDocument.objects.all()
    if params.get("document_type"):
        queryset = queryset.filter(document_type=params["document_type"])
    return queryset.order_by("document_type", "-published_at")


def get_legal(pk: Any) -> LegalDocument:
    document = LegalDocument.objects.filter(pk=pk).first()
    if document is None:
        raise NotFound()
    return document


def create_legal(*, data: dict[str, Any], actor: User, request: Any) -> LegalDocument:
    import uuid

    document = LegalDocument.objects.create(
        document_type=data["document_type"],
        version=f"draft-{uuid.uuid4().hex[:12]}",
        title=data["title"],
        sections=data["sections"],
        effective_at=data.get("effective_at"),
        status=DRAFT,
        is_current=False,
        published_at=None,
        created_by=actor,
    )
    record_audit(
        actor=actor,
        action="legal_document.created",
        object_type="LegalDocument",
        object_id=document.pk,
        request=request,
    )
    return document


def update_legal(
    *, document: LegalDocument, data: dict[str, Any], actor: User, request: Any
) -> LegalDocument:
    if document.status != DRAFT:
        raise InvalidStateTransition("Published legal documents are immutable.")
    for field in ("document_type", "title", "sections", "effective_at"):
        if field in data:
            setattr(document, field, data[field])
    document.save()
    record_audit(
        actor=actor,
        action="legal_document.updated",
        object_type="LegalDocument",
        object_id=document.pk,
        request=request,
        changed_fields=sorted(data),
    )
    return document


def _date_version(document_type: str) -> str:
    from zoneinfo import ZoneInfo

    base = timezone.now().astimezone(ZoneInfo(LIMA)).date().isoformat()
    taken = set(
        LegalDocument.objects.filter(
            document_type=document_type, version__startswith=base
        ).values_list("version", flat=True)
    )
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def publish_legal(*, document: LegalDocument, actor: User, request: Any) -> LegalDocument:
    with transaction.atomic():
        locked = LegalDocument.objects.select_for_update().get(pk=document.pk)
        if locked.status != DRAFT:
            raise InvalidStateTransition("Only drafts can be published.")
        LegalDocument.objects.filter(document_type=locked.document_type, is_current=True).update(
            is_current=False
        )
        locked.version = _date_version(locked.document_type)
        locked.status = PUBLISHED
        locked.is_current = True
        locked.published_at = timezone.now()
        locked.save()
        record_audit(
            actor=actor,
            action="legal_document.published",
            object_type="LegalDocument",
            object_id=locked.pk,
            request=request,
        )
    return locked
