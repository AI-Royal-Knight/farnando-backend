"""Anonymous public-content endpoints (contract 3.x)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.catalog.models import FAQ, PublicStats, SiteSettings
from apps.catalog.models import Testimonial as Review

pytestmark = pytest.mark.django_db


def test_site_settings_are_database_backed(client: Client) -> None:
    settings = SiteSettings.load()
    settings.contact_email = "hola@adelgaceya.pe"
    settings.save()
    response = client.get("/api/v1/public/site-settings/")
    assert response.status_code == 200
    assert response.json()["contact_email"] == "hola@adelgaceya.pe"
    assert "public" in response["Cache-Control"]


def test_faqs_are_ordered_and_anonymous(client: Client) -> None:
    FAQ.objects.create(question="Second", answer_markdown="b", position=2)
    FAQ.objects.create(question="First", answer_markdown="a", position=1)
    response = client.get("/api/v1/public/faqs/")
    assert response.status_code == 200
    assert [item["question"] for item in response.json()] == ["First", "Second"]


def test_stats_are_nullable(client: Client) -> None:
    stats = PublicStats.load()
    stats.sustained_weight_loss_percent = None
    stats.save()
    response = client.get("/api/v1/public/stats/")
    assert response.status_code == 200
    assert response.json()["sustained_weight_loss_percent"] is None


def test_complaint_categories(client: Client) -> None:
    response = client.get("/api/v1/public/complaint-categories/")
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()}
    assert {"PRODUCT", "SERVICE", "BILLING", "DELIVERY", "PRIVACY", "OTHER"} <= codes


def test_testimonials_summary_and_pagination(client: Client) -> None:
    now = timezone.now()
    for index in range(15):
        Review.objects.create(
            author_name=f"Paciente {index}",
            rating=5 if index < 10 else 4,
            body="Great",
            published_at=now - timedelta(minutes=index),
            is_published=True,
        )
    Review.objects.create(
        author_name="Hidden",
        rating=1,
        body="Hidden",
        published_at=now,
        is_published=False,
    )
    response = client.get("/api/v1/public/testimonials/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 15
    assert len(payload["results"]) == 12
    assert payload["summary"]["review_count"] == 15
    assert payload["summary"]["average_rating"] == 4.67
    assert payload["next"]


def test_testimonials_page_size_query(client: Client) -> None:
    Review.objects.create(
        author_name="Kendra L.",
        rating=5,
        body="Great",
        published_at=timezone.now(),
        is_published=True,
    )
    response = client.get("/api/v1/public/testimonials/?page_size=5")
    assert response.status_code == 200
    assert response.json()["results"][0]["author_name"] == "Kendra L."
