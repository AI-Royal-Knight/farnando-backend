"""Part 5 section 8: products, programs, packages, questionnaires and evaluation fees."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.assessments.models import Program, QuestionnaireVersion
from apps.clinical.models import Specialty
from apps.orders.models import Package, PackageItem
from apps.treatments.models import Product
from tests.factories.part3 import program

pytestmark = pytest.mark.django_db

PRODUCTS = "/api/v1/admin/products/"
PROGRAMS = "/api/v1/admin/programs/"
PACKAGES = "/api/v1/admin/packages/"
QUESTIONNAIRES = "/api/v1/admin/questionnaires/"


def product_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Tirzepatide GLP-1/GIP",
        "active_ingredient": "Tirzepatide",
        "form": "Vial",
        "route": "SUBCUTANEOUS",
        "allowed_doses": [
            {"value": 2.5, "unit": "mg", "label": "2.5 mg"},
            {"value": 5, "unit": "mg", "label": "5 mg"},
        ],
        "starting_dose_labels": ["2.5 mg"],
        "default_frequency": "WEEKLY",
        "max_refills": 3,
        "lifestyle_essentials": [{"title": "Hydration", "body": "Drink 2L of water."}],
    }
    body.update(overrides)
    return body


def program_body(product_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": "Longevity Reset",
        "category": "LONGEVITY",
        "short_description": "Metabolic longevity program.",
        "product_ids": [product_id],
        "allowed_specialty_ids": [str(Specialty.objects.get(name="Endocrinology").pk)],
        "eligibility": {"min_age": 18, "max_age": 70, "min_bmi": 25, "max_bmi": 45},
        "projection": {"months": 3, "loss_percent_by_month": [3, 6, 9]},
        "estimated_review_hours": {"min": 12, "max": 36},
    }
    body.update(overrides)
    return body


def test_admin_catalog_is_admin_only(staff_client: APIClient) -> None:
    for url in (PRODUCTS, PROGRAMS, QUESTIONNAIRES, "/api/v1/admin/content-items/"):
        assert staff_client.get(url).status_code == 403


def test_products_crud(admin_client: APIClient) -> None:
    bad = admin_client.post(PRODUCTS, product_body(starting_dose_labels=["10 mg"]), format="json")
    assert bad.status_code == 400
    created = admin_client.post(PRODUCTS, product_body(), format="json")
    assert created.status_code == 201, created.json()
    product = created.json()
    assert product["starting_dose_labels"] == ["2.5 mg"]
    assert product["lifestyle_essentials"][0]["title"] == "Hydration"
    url = f"{PRODUCTS}{product['id']}/"
    patched = admin_client.patch(url, {"max_refills": 6}, format="json")
    assert patched.json()["max_refills"] == 6
    assert admin_client.post(f"{url}deactivate/").json()["is_active"] is False
    assert admin_client.get(f"{PRODUCTS}?is_active=false").json()["count"] == 1
    assert admin_client.post(f"{url}activate/").json()["is_active"] is True
    assert admin_client.get(f"{PRODUCTS}?search=tirz").json()["count"] == 1


def test_programs_and_fee(admin_client: APIClient, api_client: APIClient) -> None:
    product = Product.objects.get(code="semaglutide-0-5")
    missing = admin_client.post(
        PROGRAMS, program_body(str(product.pk), allowed_specialty_ids=[]), format="json"
    )
    assert missing.status_code == 400
    bad_category = admin_client.post(
        PROGRAMS, program_body(str(product.pk), category="ASTROLOGY"), format="json"
    )
    assert bad_category.status_code == 400
    created = admin_client.post(PROGRAMS, program_body(str(product.pk)), format="json")
    assert created.status_code == 201, created.json()
    body = created.json()
    assert body["title"] == "Longevity Reset" and body["slug"]
    assert body["eligibility"]["max_bmi"] == 45
    assert body["projection"]["loss_percent_by_month"] == [3, 6, 9]
    url = f"{PROGRAMS}{body['id']}/"
    patched = admin_client.patch(url, {"estimated_review_hours": None}, format="json")
    assert patched.json()["estimated_review_hours"] is None
    assert admin_client.post(f"{url}deactivate/").json()["is_active"] is False

    fee_url = f"{PROGRAMS}{program().pk}/evaluation-fee/"
    assert admin_client.get(fee_url).status_code == 200
    too_high = admin_client.put(
        fee_url,
        {"amount": {"amount": "1500.00", "currency": "USD"}, "is_waived": False},
        format="json",
    )
    assert too_high.status_code == 400
    saved = admin_client.put(
        fee_url,
        {
            "amount": {"amount": "49.00", "currency": "USD"},
            "is_waived": False,
            "inclusions": ["Physician review", "Lab interpretation"],
        },
        format="json",
    )
    assert saved.status_code == 200, saved.json()
    assert saved.json()["amount"] == {"amount": "49.00", "currency": "USD"}
    prog = Program.objects.get(pk=program().pk)
    assert prog.fee_amount == 49 and prog.fee_inclusions[0] == "Physician review"


def test_packages(admin_client: APIClient, api_client: APIClient) -> None:
    product = Product.objects.get(code="semaglutide-0-5")
    base = {
        "program_id": str(program().pk),
        "name": "Quarterly Plan",
        "badge": "Most Popular",
        "supply_duration_weeks": 12,
        "billing_cadence": "QUARTERLY",
        "display_price_per_month": {"amount": "189.00", "currency": "PEN"},
        "charge_per_cycle": {"amount": "600.00", "currency": "PEN"},
        "included_services": ["Physician follow-up", "Cold-chain shipping"],
        "items": [{"product_id": str(product.pk), "quantity": 3}],
    }
    mismatch = admin_client.post(PACKAGES, base, format="json")
    assert mismatch.status_code == 400 and mismatch.json()["code"] == "price_mismatch"
    created = admin_client.post(PACKAGES, {**base, "allow_price_mismatch": True}, format="json")
    assert created.status_code == 201, created.json()
    package = created.json()
    assert package["billing_type"] == "RECURRING" and package["billing_interval_months"] == 3
    assert package["badge"] == "Most Popular"

    second_badge = admin_client.post(
        PACKAGES,
        {
            **base,
            "name": "Bi-annual",
            "billing_cadence": "BIANNUAL",
            "charge_per_cycle": {"amount": "1134.00", "currency": "PEN"},
        },
        format="json",
    )
    assert second_badge.status_code == 400
    bad_weeks = admin_client.post(
        PACKAGES,
        {**base, "badge": None, "supply_duration_weeks": 5, "allow_price_mismatch": True},
        format="json",
    )
    assert bad_weeks.status_code == 400

    url = f"{PACKAGES}{package['id']}/"
    priced = admin_client.patch(
        url, {"charge_per_cycle": {"amount": "567.00", "currency": "PEN"}}, format="json"
    )
    assert priced.status_code == 200, priced.json()
    assert priced.json()["charge_per_cycle"]["amount"] == "567.00"
    assert (
        admin_client.patch(url, {"program_id": str(program().pk)}, format="json").status_code == 400
    )

    listing = admin_client.get(f"{PROGRAMS}{program().pk}/packages/").json()
    ids = [row["id"] for row in listing["results"]]
    assert package["id"] in ids
    reordered = admin_client.post(
        f"{PROGRAMS}{program().pk}/packages/reorder/",
        {"package_ids": list(reversed(ids))},
        format="json",
    )
    assert reordered.status_code == 200
    assert [row["id"] for row in reordered.json()] == list(reversed(ids))
    assert admin_client.post(f"{url}deactivate/").json()["is_active"] is False
    assert admin_client.post(f"{url}activate/").json()["is_active"] is True


def test_questionnaire_editor(admin_client: APIClient) -> None:
    prog = program()
    published = QuestionnaireVersion.objects.get(program=prog, status="PUBLISHED")
    listing = admin_client.get(f"{QUESTIONNAIRES}?program_id={prog.pk}").json()
    assert listing["count"] >= 1 and listing["results"][0]["questions_count"] > 0

    immutable = admin_client.patch(
        f"{QUESTIONNAIRES}{published.pk}/", {"title": "Changed"}, format="json"
    )
    assert immutable.status_code == 409

    draft = admin_client.post(f"{QUESTIONNAIRES}{published.pk}/new-draft/")
    assert draft.status_code == 201, draft.json()
    draft_body = draft.json()
    assert draft_body["status"] == "DRAFT" and draft_body["version"] == published.version + 1
    assert admin_client.post(f"{QUESTIONNAIRES}{published.pk}/new-draft/").status_code == 409
    second = admin_client.post(
        QUESTIONNAIRES, {"program_id": str(prog.pk), "title": "Another"}, format="json"
    )
    assert second.status_code == 409

    durl = f"{QUESTIONNAIRES}{draft_body['id']}/"
    assert admin_client.patch(durl, {"title": "Weight loss v2"}, format="json").json()["title"] == (
        "Weight loss v2"
    )
    step = admin_client.post(
        f"{durl}steps/", {"title": "Lifestyle", "key": "lifestyle"}, format="json"
    )
    assert step.status_code == 201, step.json()
    dup = admin_client.post(f"{durl}steps/", {"title": "Again", "key": "lifestyle"}, format="json")
    assert dup.status_code == 400
    step_id = next(s["id"] for s in step.json()["steps"] if s["key"] == "lifestyle")

    question_url = f"/api/v1/admin/questionnaire-steps/{step_id}/questions/"
    one_option = admin_client.post(
        question_url,
        {
            "text": "How often do you exercise?",
            "type": "SINGLE_CHOICE",
            "required": True,
            "review_group": "OTHER",
            "options": [{"label": "Never"}],
        },
        format="json",
    )
    assert one_option.status_code == 400
    question = admin_client.post(
        question_url,
        {
            "text": "How often do you exercise?",
            "type": "SINGLE_CHOICE",
            "required": True,
            "review_group": "OTHER",
            "options": [
                {"label": "Never", "screening_effect": "NONE"},
                {"label": "Weekly", "is_exclusive": True},
            ],
        },
        format="json",
    )
    assert question.status_code == 201, question.json()
    question_id = question.json()["id"]
    follow_up = admin_client.post(
        question_url,
        {
            "text": "Tell us more about it",
            "type": "TEXT",
            "required": False,
            "review_group": "OTHER",
            "max_length": 300,
            "show_if": {
                "question_id": question_id,
                "equals": [question.json()["options"][1]["id"]],
            },
        },
        format="json",
    )
    assert follow_up.status_code == 201, follow_up.json()
    text_without_length = admin_client.post(
        question_url,
        {"text": "Notes please", "type": "TEXT", "required": False, "review_group": "OTHER"},
        format="json",
    )
    assert text_without_length.status_code == 400

    patched = admin_client.patch(
        f"/api/v1/admin/questions/{question_id}/", {"help_text": "Be honest"}, format="json"
    )
    assert patched.json()["help_text"] == "Be honest"

    detail = admin_client.get(durl).json()
    order = [
        {"id": s["id"], "question_ids": [q["id"] for q in s["questions"]]} for s in detail["steps"]
    ]
    reordered = admin_client.post(f"{durl}reorder/", {"steps": order}, format="json")
    assert reordered.status_code == 200, reordered.json()
    incomplete = admin_client.post(f"{durl}reorder/", {"steps": order[:1]}, format="json")
    assert incomplete.status_code == 400

    validated = admin_client.post(f"{durl}validate/").json()
    assert validated == {"valid": True, "errors": []}
    no_confirm = admin_client.post(f"{durl}publish/", {"confirm": False}, format="json")
    assert no_confirm.status_code == 400
    published_now = admin_client.post(
        f"{durl}publish/", {"confirm": True, "note": "Signed off by Dr. Ruiz"}, format="json"
    )
    assert published_now.status_code == 200, published_now.json()
    assert published_now.json()["status"] == "PUBLISHED"
    published.refresh_from_db()
    assert published.status == "ARCHIVED"

    measurements = next(s for s in detail["steps"] if s["type"] == "MEASUREMENTS")
    locked = admin_client.delete(f"/api/v1/admin/questionnaire-steps/{measurements['id']}/")
    assert locked.status_code == 409

    off = admin_client.post(f"{durl}deactivate/")
    assert off.json()["is_active"] is False
    assert Program.objects.get(pk=prog.pk).is_available is False
    assert admin_client.post(f"{durl}activate/").json()["is_active"] is True


def test_questionnaire_draft_edits_and_deletes(admin_client: APIClient) -> None:
    product = Product.objects.get(code="semaglutide-0-5")
    new_program = admin_client.post(PROGRAMS, program_body(str(product.pk)), format="json").json()
    created = admin_client.post(
        QUESTIONNAIRES,
        {"program_id": new_program["id"], "title": "Longevity intake"},
        format="json",
    )
    assert created.status_code == 201
    qid = created.json()["id"]
    assert [s["type"] for s in created.json()["steps"]] == ["MEASUREMENTS"]
    invalid = admin_client.post(f"{QUESTIONNAIRES}{qid}/validate/").json()
    assert invalid["valid"] is False
    assert (
        admin_client.post(
            f"{QUESTIONNAIRES}{qid}/publish/", {"confirm": True}, format="json"
        ).status_code
        == 400
    )
    step = admin_client.post(
        f"{QUESTIONNAIRES}{qid}/steps/", {"title": "Health", "key": "health"}, format="json"
    ).json()
    step_id = next(s["id"] for s in step["steps"] if s["key"] == "health")
    renamed = admin_client.patch(
        f"/api/v1/admin/questionnaire-steps/{step_id}/", {"title": "Health history"}, format="json"
    )
    assert renamed.status_code == 200
    question = admin_client.post(
        f"/api/v1/admin/questionnaire-steps/{step_id}/questions/",
        {
            "text": "Any allergies?",
            "type": "YES_NO",
            "required": True,
            "review_group": "CURRENT_MEDICATIONS",
        },
        format="json",
    ).json()
    assert admin_client.delete(f"/api/v1/admin/questions/{question['id']}/").status_code == 204
    assert admin_client.delete(f"/api/v1/admin/questionnaire-steps/{step_id}/").status_code == 204


def test_partial_package_update_keeps_one_time_billing(admin_client: APIClient) -> None:
    """Renaming a legacy one-time package (no cadence) must not make it recurring."""
    product = Product.objects.get(code="semaglutide-0-5")
    package = Package.objects.create(
        program=program(),
        name="Legacy one-time",
        supply_duration_weeks=4,
        billing_cadence="",
        billing_type="ONE_TIME",
        display_price_per_month=Decimal("599.00"),
        charge_per_cycle=Decimal("599.00"),
        currency="PEN",
    )
    PackageItem.objects.create(package=package, product=product, quantity=1, position=0)
    response = admin_client.patch(
        f"{PACKAGES}{package.pk}/",
        {"name": "Renamed", "included_services": ["Physician review"]},
        format="json",
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["billing_type"] == "ONE_TIME" and body["billing_interval_months"] is None
