from __future__ import annotations

from django.urls import path

from apps.support import views

app_name = "support"

urlpatterns = [
    path(
        "public/contact-messages/",
        views.ContactMessageCreateView.as_view(),
        name="contact-messages",
    ),
    path("public/complaints/", views.ComplaintCreateView.as_view(), name="complaints"),
    path(
        "public/complaints/<str:complaint_number>/",
        views.ComplaintStatusView.as_view(),
        name="complaint-status",
    ),
]
