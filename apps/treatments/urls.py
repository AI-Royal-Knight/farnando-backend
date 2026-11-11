from __future__ import annotations

from django.urls import path

from apps.treatments import views

app_name = "treatments"

urlpatterns = [
    path("treatments/", views.TreatmentListView.as_view(), name="treatments"),
    path(
        "treatments/<uuid:treatment_id>/",
        views.TreatmentDetailView.as_view(),
        name="treatment-detail",
    ),
    path(
        "treatments/<uuid:treatment_id>/prescription/download/",
        views.TreatmentPrescriptionDownloadView.as_view(),
        name="treatment-prescription-download",
    ),
    path(
        "treatments/<uuid:treatment_id>/renewal-requests/",
        views.TreatmentRenewalRequestView.as_view(),
        name="treatment-renewal-requests",
    ),
]
