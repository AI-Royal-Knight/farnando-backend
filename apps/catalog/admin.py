"""Admin editing for public content."""

from __future__ import annotations

from typing import Any

from django.contrib import admin

from apps.catalog.models import FAQ, ComplaintCategory, PublicStats, SiteSettings, Testimonial


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def has_add_permission(self, request: Any) -> bool:
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("question", "position")
    ordering = ("position",)


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "author_name",
        "rating",
        "is_published",
        "patient_consent_recorded",
        "published_at",
    )
    list_filter = ("is_published", "patient_consent_recorded", "rating")
    search_fields = ("author_name", "body")


@admin.register(PublicStats)
class PublicStatsAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    def has_add_permission(self, request: Any) -> bool:
        return not PublicStats.objects.exists()

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False


@admin.register(ComplaintCategory)
class ComplaintCategoryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("code", "label", "is_active", "position")
    list_filter = ("is_active",)
    ordering = ("position", "code")
