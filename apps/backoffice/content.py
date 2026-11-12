"""Content items (Part 5, 9.1 and amendment B1) and public image uploads (9.4)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.assessments.models import Program
from apps.catalog.models import AssetStatus, AssetUpload, ContentItem, ContentStatus
from apps.core.audit import record_audit
from apps.core.exceptions import FileTooLarge, InvalidStateTransition
from apps.core.images import IMAGE_EXT, ImageRejected, sanitize_image
from apps.core.storage import delete_object, object_exists, presigned_upload_url, presigned_url

UPLOAD_TTL = 15 * 60
PUBLIC_CACHE_KEY = "public:content-items:v1"
PUBLIC_CACHE_SECONDS = 300


# ---------------------------------------------------------------------------
# Assets (9.4)
# ---------------------------------------------------------------------------


def public_asset_url(asset: AssetUpload) -> str:
    base = str(settings.PUBLIC_ASSET_BASE_URL or "").rstrip("/")
    if base:
        return f"{base}/{asset.storage_key}"
    # Without a CDN (development) fall back to a long-lived signed URL.
    return presigned_url(asset.storage_key, expires_in=7 * 24 * 3600)


def start_upload(*, data: dict[str, Any], actor: User) -> tuple[AssetUpload, dict[str, Any]]:
    content_type = data["content_type"]
    size = int(data["size_bytes"])
    if size < 1 or size > settings.ASSET_MAX_BYTES:
        raise FileTooLarge()
    key = f"public-assets/{uuid.uuid4().hex}.{IMAGE_EXT[content_type]}"
    asset = AssetUpload.objects.create(
        purpose=data["purpose"],
        storage_key=key,
        content_type=content_type,
        size_bytes=size,
        uploaded_by=actor,
    )
    url, headers = presigned_upload_url(key, content_type=content_type, expires_in=UPLOAD_TTL)
    return asset, {
        "method": "PUT",
        "url": url,
        "headers": headers,
        "expires_at": timezone.now() + timedelta(seconds=UPLOAD_TTL),
    }


def _reject(asset: AssetUpload) -> None:
    asset.status = AssetStatus.REJECTED
    asset.save(update_fields=["status", "updated_at"])


def confirm_upload(*, asset_id: Any, actor: User, request: Any) -> AssetUpload:
    asset = AssetUpload.objects.filter(pk=asset_id).first()
    if asset is None:
        raise NotFound()
    if asset.status != AssetStatus.PENDING_UPLOAD:
        raise InvalidStateTransition()
    if not object_exists(asset.storage_key):
        raise ValidationError({"upload": ["The file was not uploaded."]})
    with default_storage.open(asset.storage_key, "rb") as handle:
        raw = handle.read(settings.ASSET_MAX_BYTES + 1)
    if len(raw) > settings.ASSET_MAX_BYTES:
        _reject(asset)
        raise FileTooLarge()
    try:
        clean, content_type = sanitize_image(raw, max_pixels=settings.ASSET_MAX_PIXELS)
    except ImageRejected as exc:
        _reject(asset)
        raise ValidationError({"upload": [str(exc)]}) from exc
    delete_object(asset.storage_key)
    default_storage.save(asset.storage_key, ContentFile(clean))
    asset.status = AssetStatus.READY
    asset.content_type = content_type
    asset.size_bytes = len(clean)
    asset.save(update_fields=["status", "content_type", "size_bytes", "updated_at"])
    record_audit(
        actor=actor,
        action="asset.uploaded",
        object_type="AssetUpload",
        object_id=asset.pk,
        request=request,
    )
    return asset


# ---------------------------------------------------------------------------
# Content items (9.1)
# ---------------------------------------------------------------------------


def _money(value: dict[str, Any] | None) -> dict[str, str] | None:
    if not value:
        return None
    return {"amount": f"{Decimal(str(value['amount'])):.2f}", "currency": str(value["currency"])}


def public_payload(item: ContentItem) -> dict[str, Any]:
    pricing = item.pricing or {}
    return {
        "id": item.pk,
        "category": item.category,
        "title": item.title,
        "accent_phrase": item.accent_phrase or None,
        "description": item.description,
        "product_image_url": public_asset_url(item.product_image) if item.product_image else None,
        "lifestyle_image_urls": [
            public_asset_url(asset) for asset in item.lifestyle_images.order_by("created_at")
        ],
        "benefits": list(item.benefits or []),
        "pricing": {
            "price_from": _money(pricing.get("price_from")),
            "price_to": _money(pricing.get("price_to")),
            "period": pricing.get("period"),
            "note": pricing.get("note"),
        },
        "cta_text": item.cta_text or None,
        "position": item.position,
    }


def admin_payload(item: ContentItem) -> dict[str, Any]:
    body = public_payload(item)
    body.update(
        {
            "status": item.status,
            "product_image_upload_id": item.product_image_id,
            "lifestyle_image_upload_ids": [
                asset.pk for asset in item.lifestyle_images.order_by("created_at")
            ],
            "pricing": item.pricing,
            "program_id": item.program_id,
            "published_at": item.published_at,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
    )
    return body


def public_items() -> list[dict[str, Any]]:
    cached = cache.get(PUBLIC_CACHE_KEY)
    if cached is not None:
        return list(cached)
    items = (
        ContentItem.objects.filter(status=ContentStatus.PUBLISHED)
        .select_related("product_image")
        .prefetch_related("lifestyle_images")
        .order_by("position", "-created_at")
    )
    payload = [public_payload(item) for item in items]
    cache.set(PUBLIC_CACHE_KEY, payload, PUBLIC_CACHE_SECONDS)
    return payload


def _invalidate() -> None:
    transaction.on_commit(lambda: cache.delete(PUBLIC_CACHE_KEY))


def filtered(params: Any) -> QuerySet[ContentItem]:
    queryset = ContentItem.objects.select_related("product_image").prefetch_related(
        "lifestyle_images"
    )
    if params.get("category"):
        queryset = queryset.filter(category=params["category"])
    if params.get("status"):
        queryset = queryset.filter(status=params["status"])
    if params.get("search"):
        term = params["search"]
        queryset = queryset.filter(Q(title__icontains=term) | Q(description__icontains=term))
    return queryset.order_by("position", "-created_at")


def get(item_id: Any) -> ContentItem:
    item = filtered({}).filter(pk=item_id).first()
    if item is None:
        raise NotFound()
    return item


def _ready(asset_id: Any, field: str) -> AssetUpload:
    asset = AssetUpload.objects.filter(pk=asset_id, status=AssetStatus.READY).first()
    if asset is None:
        raise ValidationError({field: ["Unknown or unconfirmed upload."]})
    return asset


def _check_accent(title: str, accent: str) -> None:
    if accent and accent.lower() not in title.lower():
        raise ValidationError({"accent_phrase": ["Must be part of the title."]})


def _check_pricing(pricing: dict[str, Any] | None) -> None:
    if not pricing:
        return
    low, high = pricing["price_from"], pricing["price_to"]
    if low["currency"] != high["currency"]:
        raise ValidationError({"pricing": ["Both prices must use the same currency."]})
    if Decimal(str(high["amount"])) < Decimal(str(low["amount"])):
        raise ValidationError({"pricing": ["price_to must be at least price_from."]})


def _serialize_pricing(pricing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pricing:
        return None
    return {
        "price_from": _money(pricing["price_from"]),
        "price_to": _money(pricing["price_to"]),
        "period": pricing["period"],
        "note": pricing.get("note") or None,
    }


SIMPLE_FIELDS = ("title", "accent_phrase", "description", "category", "benefits", "cta_text")


def save(
    *, item: ContentItem | None, data: dict[str, Any], actor: User, request: Any
) -> ContentItem:
    creating = item is None
    target = item or ContentItem()
    title = data.get("title", target.title)
    accent = data.get("accent_phrase", target.accent_phrase) or ""
    _check_accent(title, accent)
    if "pricing" in data:
        _check_pricing(data["pricing"])
    if creating and data.get("status") == ContentStatus.PUBLISHED:
        target.published_at = timezone.now()
    with transaction.atomic():
        for field in SIMPLE_FIELDS:
            if field in data:
                value = data[field]
                setattr(target, field, value if value is not None else "")
        if "status" in data:
            if data["status"] == ContentStatus.PUBLISHED and target.status != data["status"]:
                target.published_at = timezone.now()
            target.status = data["status"]
        if "position" in data:
            target.position = data["position"]
        if "pricing" in data:
            target.pricing = _serialize_pricing(data["pricing"])
        if "product_image_upload_id" in data:
            upload_id = data["product_image_upload_id"]
            target.product_image = (
                _ready(upload_id, "product_image_upload_id") if upload_id else None
            )
        if "program_id" in data:
            program_id = data["program_id"]
            if program_id and not Program.objects.filter(pk=program_id).exists():
                raise ValidationError({"program_id": ["Unknown program."]})
            target.program_id = program_id
        target.save()
        if "lifestyle_image_upload_ids" in data:
            ids = data["lifestyle_image_upload_ids"] or []
            target.lifestyle_images.set(
                [_ready(upload_id, "lifestyle_image_upload_ids") for upload_id in ids]
            )
        record_audit(
            actor=actor,
            action="content_item.created" if creating else "content_item.updated",
            object_type="ContentItem",
            object_id=target.pk,
            request=request,
            changed_fields=sorted(data),
        )
        _invalidate()
    return get(target.pk)


def set_status(*, item: ContentItem, publish: bool, actor: User, request: Any) -> ContentItem:
    if publish:
        missing = [f for f in ("title", "description", "category") if not getattr(item, f)]
        if missing:
            raise ValidationError({field: ["Required to publish."] for field in missing})
        if item.status != ContentStatus.PUBLISHED:
            item.published_at = timezone.now()
        item.status = ContentStatus.PUBLISHED
    else:
        item.status = ContentStatus.DRAFT
    item.save(update_fields=["status", "published_at", "updated_at"])
    record_audit(
        actor=actor,
        action="content_item.published" if publish else "content_item.unpublished",
        object_type="ContentItem",
        object_id=item.pk,
        request=request,
    )
    _invalidate()
    return item


def delete(*, item: ContentItem, actor: User, request: Any) -> None:
    if item.status != ContentStatus.DRAFT:
        raise InvalidStateTransition("Only drafts can be deleted. Unpublish it first.")
    pk = item.pk
    item.delete()
    record_audit(
        actor=actor,
        action="content_item.deleted",
        object_type="ContentItem",
        object_id=pk,
        request=request,
    )
    _invalidate()
