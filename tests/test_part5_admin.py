"""Part 5 sections 9-11: content, reference data, legal, settings, reports, audit, privacy."""

from __future__ import annotations

import io
from datetime import timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.backoffice.models import PrivacyRequest
from apps.catalog.models import AssetUpload, ComplaintCategory, TicketCategory
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


def uploaded_asset(admin_client: APIClient, purpose: str = "CONTENT_IMAGE") -> str:
    started = admin_client.post(
        "/api/v1/admin/asset-uploads/",
        {"purpose": purpose, "content_type": "image/png", "size_bytes": 2000},
        format="json",
    )
    assert started.status_code == 201, started.json()
    assert started.json()["upload"]["method"] == "PUT"
    asset = AssetUpload.objects.get(pk=started.json()["id"])
    default_storage.save(asset.storage_key, ContentFile(png_bytes()))
    confirmed = admin_client.post(f"/api/v1/admin/asset-uploads/{asset.pk}/confirm/")
    assert confirmed.status_code == 200, confirmed.json()
    assert confirmed.json()["status"] == "READY" and confirmed.json()["url"]
    return str(asset.pk)


def content_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Semaglutide",
        "accent_phrase": "Semaglutide",
        "description": "A weekly injection supervised by our physicians.",
        "category": "MEDICINE",
        "benefits": ["Physician supervised", "Cold-chain delivery"],
        "pricing": {
            "price_from": {"amount": "189.00", "currency": "PEN"},
            "price_to": {"amount": "249.00", "currency": "PEN"},
            "period": "MONTH",
        },
        "cta_text": "Start now",
    }
    body.update(overrides)
    return body


def test_asset_upload_rules(admin_client: APIClient) -> None:
    too_big = admin_client.post(
        "/api/v1/admin/asset-uploads/",
        {"purpose": "CONTENT_IMAGE", "content_type": "image/png", "size_bytes": 50_000_000},
        format="json",
    )
    assert too_big.status_code in (400, 413)
    started = admin_client.post(
        "/api/v1/admin/asset-uploads/",
        {"purpose": "CONTENT_IMAGE", "content_type": "image/png", "size_bytes": 100},
        format="json",
    ).json()
    missing = admin_client.post(f"/api/v1/admin/asset-uploads/{started['id']}/confirm/")
    assert missing.status_code == 400
    asset = AssetUpload.objects.get(pk=started["id"])
    default_storage.save(asset.storage_key, ContentFile(b"not an image at all"))
    rejected = admin_client.post(f"/api/v1/admin/asset-uploads/{started['id']}/confirm/")
    assert rejected.status_code == 400
    asset.refresh_from_db()
    assert asset.status == "REJECTED"
    again = admin_client.post(f"/api/v1/admin/asset-uploads/{started['id']}/confirm/")
    assert again.status_code == 409


def test_content_items_lifecycle(admin_client: APIClient, api_client: APIClient) -> None:
    image_id = uploaded_asset(admin_client)
    lifestyle_id = uploaded_asset(admin_client)
    created = admin_client.post(
        "/api/v1/admin/content-items/",
        content_body(product_image_upload_id=image_id, lifestyle_image_upload_ids=[lifestyle_id]),
        format="json",
    )
    assert created.status_code == 201, created.json()
    item = created.json()
    assert item["product_image_url"] and len(item["lifestyle_image_urls"]) == 1
    url = f"/api/v1/admin/content-items/{item['id']}/"

    assert api_client.get("/api/v1/public/content-items/").json() == []
    preview = admin_client.get(f"{url}preview/").json()
    assert preview["title"] == "Semaglutide" and preview["pricing"]["period"] == "MONTH"
    assert "status" not in preview

    published = admin_client.post(f"{url}publish/")
    assert published.status_code == 200 and published.json()["status"] == "PUBLISHED"
    cache.clear()
    public = api_client.get("/api/v1/public/content-items/")
    assert public.status_code == 200
    assert public["Cache-Control"] == "public, max-age=300"
    assert [row["id"] for row in public.json()] == [item["id"]]

    assert admin_client.delete(url).status_code == 409
    patched = admin_client.patch(url, {"title": "Semaglutide Plus"}, format="json")
    assert patched.json()["title"] == "Semaglutide Plus"
    listing = admin_client.get("/api/v1/admin/content-items/?status=PUBLISHED&search=plus").json()
    assert listing["count"] == 1
    assert admin_client.post(f"{url}unpublish/").json()["status"] == "DRAFT"
    assert admin_client.delete(url).status_code == 204
    assert admin_client.get(url).status_code == 404


def test_content_rejects_bad_assets(admin_client: APIClient) -> None:
    pending = admin_client.post(
        "/api/v1/admin/asset-uploads/",
        {"purpose": "CONTENT_IMAGE", "content_type": "image/png", "size_bytes": 100},
        format="json",
    ).json()
    bad = admin_client.post(
        "/api/v1/admin/content-items/",
        content_body(product_image_upload_id=pending["id"]),
        format="json",
    )
    assert bad.status_code == 400
    short = admin_client.post(
        "/api/v1/admin/content-items/", content_body(description="short"), format="json"
    )
    assert short.status_code == 400


@pytest.mark.parametrize(
    ("resource", "body", "update"),
    [
        (
            "faqs",
            {
                "question": "Is it safe?",
                "answer_markdown": "Yes, supervised.",
                "position": 1,
                "is_published": True,
            },
            {"position": 3},
        ),
        (
            "testimonials",
            {
                "author_name": "Ana",
                "rating": 5,
                "body": "Great experience overall.",
                "patient_consent_recorded": True,
                "is_published": True,
            },
            {"rating": 4},
        ),
        (
            "complaint-categories",
            {"code": "BILLING_X", "label": "Billing", "position": 9, "is_active": True},
            {"label": "Billing issues"},
        ),
        (
            "support-ticket-categories",
            {
                "code": "SHIPPING_X",
                "label": "Shipping",
                "requires_clinical_staff": False,
                "position": 9,
                "is_active": True,
            },
            {"requires_clinical_staff": True},
        ),
        (
            "specialties",
            {"name": "Sports Medicine", "requires_registration_number": True, "is_active": True},
            {"requires_registration_number": False},
        ),
        (
            "consultation-types",
            {
                "code": "NUTRITION_X",
                "name": "Nutrition",
                "description": "",
                "duration_minutes": 30,
                "is_initial": False,
                "is_active": True,
            },
            {"duration_minutes": 45},
        ),
        (
            "lab-panels",
            {
                "code": "LIPID_X",
                "name": "Lipid panel",
                "description": "Cholesterol",
                "is_active": True,
            },
            {"name": "Full lipid panel"},
        ),
    ],
)
def test_reference_crud(
    admin_client: APIClient, resource: str, body: dict[str, Any], update: dict[str, Any]
) -> None:
    base = f"/api/v1/admin/{resource}/"
    created = admin_client.post(base, body, format="json")
    assert created.status_code == 201, created.json()
    url = f"{base}{created.json()['id']}/"
    patched = admin_client.patch(url, update, format="json")
    assert patched.status_code == 200, patched.json()
    for key, value in update.items():
        assert patched.json()[key] == value
    assert admin_client.get(url).status_code == 200
    assert admin_client.get(f"{base}?search={str(next(iter(body.values())))[:4]}").json()["count"]
    assert admin_client.post(base, {**body, "unknown": 1}, format="json").status_code == 400
    assert admin_client.delete(url).status_code == 204
    assert admin_client.get(url).status_code == 404


def test_reference_rules(admin_client: APIClient, patient_user: User) -> None:
    no_consent = admin_client.post(
        "/api/v1/admin/testimonials/",
        {
            "author_name": "Ana",
            "rating": 5,
            "body": "Great experience overall.",
            "patient_consent_recorded": False,
            "is_published": True,
        },
        format="json",
    )
    assert no_consent.status_code == 400
    bad_duration = admin_client.post(
        "/api/v1/admin/consultation-types/",
        {
            "code": "ODD_X",
            "name": "Odd",
            "description": "",
            "duration_minutes": 33,
            "is_initial": False,
            "is_active": True,
        },
        format="json",
    )
    assert bad_duration.status_code == 400
    category = TicketCategory.objects.filter(is_active=True).first()
    assert category is not None
    dup = admin_client.post(
        "/api/v1/admin/support-ticket-categories/",
        {
            "code": category.code,
            "label": "Dup",
            "requires_clinical_staff": False,
            "position": 1,
            "is_active": True,
        },
        format="json",
    )
    assert dup.status_code == 400

    from apps.support.models import SupportTicket

    SupportTicket.objects.create(
        patient=patient_user, category=category, subject="Help", status="OPEN"
    )
    deleted = admin_client.delete(f"/api/v1/admin/support-ticket-categories/{category.pk}/")
    assert deleted.status_code == 204
    category.refresh_from_db()
    assert category.is_active is False
    complaint = ComplaintCategory.objects.first()
    assert complaint is not None
    assert admin_client.get("/api/v1/admin/complaint-categories/?is_active=true").json()["count"]


def test_legal_documents(admin_client: APIClient) -> None:
    created = admin_client.post(
        "/api/v1/admin/legal-documents/",
        {
            "document_type": "PRIVACY_POLICY",
            "title": "Privacy Policy",
            "sections": [{"heading": "Data", "body_markdown": "We protect your data."}],
        },
        format="json",
    )
    assert created.status_code == 201, created.json()
    document = created.json()
    assert document["status"] == "DRAFT" and document["version"] is None
    url = f"/api/v1/admin/legal-documents/{document['id']}/"
    assert admin_client.patch(url, {"title": "Privacy Notice"}, format="json").status_code == 200
    published = admin_client.post(f"{url}publish/")
    assert published.status_code == 200, published.json()
    body = published.json()
    assert body["status"] == "PUBLISHED" and body["is_current"] is True
    assert len(body["version"]) >= 10
    assert admin_client.patch(url, {"title": "Again"}, format="json").status_code == 409
    assert admin_client.post(f"{url}publish/").status_code == 409

    second = admin_client.post(
        "/api/v1/admin/legal-documents/",
        {
            "document_type": "PRIVACY_POLICY",
            "title": "Privacy Policy v2",
            "sections": [{"heading": "Data", "body_markdown": "Updated."}],
        },
        format="json",
    ).json()
    newer = admin_client.post(f"/api/v1/admin/legal-documents/{second['id']}/publish/").json()
    assert newer["version"] == f"{body['version']}-2"
    listing = admin_client.get("/api/v1/admin/legal-documents/?document_type=PRIVACY_POLICY")
    current = [row for row in listing.json()["results"] if row["is_current"]]
    assert [row["id"] for row in current] == [second["id"]]


def test_settings_groups(admin_client: APIClient) -> None:
    assert admin_client.get("/api/v1/admin/settings/unknown/").status_code == 404
    support = admin_client.get("/api/v1/admin/settings/support/")
    assert support.status_code == 200
    body = {"first_response_sla_minutes": 90, "auto_close_resolved_days": 5}
    no_reason = admin_client.put("/api/v1/admin/settings/support/", body, format="json")
    assert no_reason.status_code == 400
    saved = admin_client.put(
        "/api/v1/admin/settings/support/", {**body, "reason": "Faster SLA"}, format="json"
    )
    assert saved.status_code == 200, saved.json()
    assert saved.json()["first_response_sla_minutes"] == 90
    assert admin_client.get("/api/v1/admin/settings/support/").json() == saved.json()
    log = AuditLog.objects.filter(object_type="Settings").latest("created_at")
    assert log.metadata.get("reason") == "Faster SLA"
    invalid = admin_client.put(
        "/api/v1/admin/settings/support/",
        {"first_response_sla_minutes": 0, "auto_close_resolved_days": 5, "reason": "Broken"},
        format="json",
    )
    assert invalid.status_code == 400
    for group in ("site", "stats", "evaluation", "booking", "complaints", "messaging", "payments"):
        current = admin_client.get(f"/api/v1/admin/settings/{group}/")
        assert current.status_code == 200
        resaved = admin_client.put(
            f"/api/v1/admin/settings/{group}/",
            {**current.json(), "reason": "Round trip"},
            format="json",
        )
        assert resaved.status_code == 200, (group, resaved.json())


def test_reports(
    admin_client: APIClient, staff_client: APIClient, django_capture_on_commit_callbacks: Any
) -> None:
    assert staff_client.get("/api/v1/admin/reports/overview/").status_code == 403
    overview = admin_client.get("/api/v1/admin/reports/overview/")
    assert overview.status_code == 200, overview.json()
    assert "kpis" in overview.json()
    too_long = admin_client.get("/api/v1/admin/reports/overview/?from=2020-01-01&to=2026-01-01")
    assert too_long.status_code == 400
    reversed_range = admin_client.get(
        "/api/v1/admin/reports/overview/?from=2026-05-01&to=2026-01-01"
    )
    assert reversed_range.status_code == 400
    assert admin_client.get("/api/v1/admin/reports/overview/?timezone=Mars/Base").status_code == 400
    for granularity in ("DAY", "WEEK", "MONTH"):
        series = admin_client.get(
            f"/api/v1/admin/reports/revenue/?granularity={granularity}&compare=PREVIOUS_PERIOD"
        )
        assert series.status_code == 200, series.json()
        assert series.json()["granularity"] == granularity
    assert admin_client.get("/api/v1/admin/reports/revenue/?granularity=HOUR").status_code == 400

    today = timezone.localdate()
    for kind in ("OVERVIEW", "REVENUE", "EVALUATIONS", "ORDERS", "SUBSCRIPTIONS"):
        with django_capture_on_commit_callbacks(execute=True):
            requested = admin_client.post(
                "/api/v1/admin/reports/exports/",
                {"report": kind, "from": str(today - timedelta(days=30)), "to": str(today)},
                format="json",
            )
        assert requested.status_code == 202, requested.json()
        status = admin_client.get(f"/api/v1/admin/reports/exports/{requested.json()['id']}/")
        assert status.status_code == 200
        assert status.json()["status"] == "READY" and status.json()["download_url"]
    missing = admin_client.post(
        "/api/v1/admin/reports/exports/", {"report": "OVERVIEW"}, format="json"
    )
    assert missing.status_code == 400


def test_audit_logs(admin_client: APIClient) -> None:
    admin_client.put(
        "/api/v1/admin/settings/complaints/",
        {"response_sla_hours": 48, "reason": "Legal update"},
        format="json",
    )
    logs = admin_client.get("/api/v1/admin/audit-logs/?object_type=Settings")
    assert logs.status_code == 200
    assert logs.json()["count"] >= 1
    today = timezone.localdate().isoformat()
    ranged = admin_client.get(f"/api/v1/admin/audit-logs/?from={today}&to={today}")
    assert ranged.status_code == 200 and ranged.json()["count"] >= 1
    assert admin_client.post("/api/v1/admin/audit-logs/", {}, format="json").status_code == 405


def test_privacy_requests(
    auth_client: APIClient, admin_client: APIClient, patient_user: User
) -> None:
    export = auth_client.post(
        "/api/v1/patients/me/privacy-requests/", {"type": "ACCESS_EXPORT"}, format="json"
    )
    assert export.status_code == 201, export.json()
    assert export.json()["status"] == "RECEIVED"
    rectify = auth_client.post(
        "/api/v1/patients/me/privacy-requests/",
        {"type": "RECTIFICATION", "details": "My birth date is wrong."},
        format="json",
    )
    assert rectify.status_code == 201
    assert len(auth_client.get("/api/v1/patients/me/privacy-requests/").json()) == 2

    listing = admin_client.get("/api/v1/admin/privacy-requests/?status=RECEIVED").json()
    assert listing["count"] == 2
    export_url = f"/api/v1/admin/privacy-requests/{export.json()['id']}/"
    assert admin_client.get(export_url).status_code == 200
    rectify_url = f"/api/v1/admin/privacy-requests/{rectify.json()['id']}/"
    assert admin_client.post(f"{rectify_url}fulfill/").status_code == 409
    no_note = admin_client.post(f"{rectify_url}status/", {"status": "REJECTED"}, format="json")
    assert no_note.status_code == 400
    progress = admin_client.post(f"{rectify_url}status/", {"status": "IN_PROGRESS"}, format="json")
    assert progress.json()["status"] == "IN_PROGRESS"
    done = admin_client.post(
        f"{rectify_url}status/", {"status": "COMPLETED", "note": "Fixed."}, format="json"
    )
    assert done.json()["status"] == "COMPLETED"
    assert (
        admin_client.post(
            f"{rectify_url}status/", {"status": "IN_PROGRESS"}, format="json"
        ).status_code
        == 409
    )

    fulfilled = admin_client.post(f"{export_url}fulfill/")
    assert fulfilled.status_code == 200, fulfilled.json()
    assert fulfilled.json()["status"] == "COMPLETED"
    mine = auth_client.get("/api/v1/patients/me/privacy-requests/").json()
    exported = next(row for row in mine if row["type"] == "ACCESS_EXPORT")
    assert exported["download_url"]

    deletion = auth_client.post(
        "/api/v1/patients/me/privacy-requests/", {"type": "DELETION"}, format="json"
    )
    assert deletion.status_code == 201
    throttled = auth_client.post(
        "/api/v1/patients/me/privacy-requests/", {"type": "DELETION"}, format="json"
    )
    assert throttled.status_code == 429
    gone = admin_client.post(f"/api/v1/admin/privacy-requests/{deletion.json()['id']}/fulfill/")
    assert gone.status_code == 200, gone.json()
    patient_user.refresh_from_db()
    assert patient_user.is_active is False and patient_user.email.endswith("@deleted.invalid")
    assert PrivacyRequest.objects.filter(status="COMPLETED").count() == 3


def test_login_is_audited(api_client: APIClient, patient_user: User) -> None:
    from tests.test_accounts_auth import api_login

    response = api_login(api_client, patient_user)
    assert response.status_code == 200
    entry = AuditLog.objects.filter(action="auth.login", actor=patient_user).get()
    assert entry.action_type == "LOGIN" and entry.metadata["method"] == "PASSWORD"


def test_support_jobs_close_resolved_tickets(patient_user: User) -> None:
    from apps.support.models import SupportTicket
    from apps.support.tasks import run_support_jobs

    category = TicketCategory.objects.filter(is_active=True).first()
    ticket = SupportTicket.objects.create(
        patient=patient_user,
        category=category,
        subject="Done",
        status="RESOLVED",
        resolved_at=timezone.now() - timedelta(days=30),
    )
    assert run_support_jobs() == {"auto_closed": 1}
    ticket.refresh_from_db()
    assert ticket.status == "CLOSED"
