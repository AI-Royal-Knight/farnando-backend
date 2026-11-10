from __future__ import annotations

from django.urls import path

from apps.catalog import views

app_name = "catalog"

urlpatterns = [
    path("public/site-settings/", views.PublicSiteSettingsView.as_view(), name="site-settings"),
    path("public/faqs/", views.PublicFAQView.as_view(), name="faqs"),
    path("public/testimonials/", views.PublicTestimonialsView.as_view(), name="testimonials"),
    path("public/stats/", views.PublicStatsView.as_view(), name="stats"),
    path(
        "public/complaint-categories/",
        views.PublicComplaintCategoriesView.as_view(),
        name="complaint-categories",
    ),
]
