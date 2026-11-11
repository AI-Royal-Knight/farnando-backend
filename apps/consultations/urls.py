from __future__ import annotations

from django.urls import path

from apps.consultations import views

app_name = "consultations"

urlpatterns = [
    path(
        "consultation-types/",
        views.ConsultationTypeListView.as_view(),
        name="consultation-types",
    ),
    path("physicians/", views.PhysicianListView.as_view(), name="physicians"),
    path(
        "physicians/<uuid:physician_id>/availability/",
        views.PhysicianAvailabilityView.as_view(),
        name="physician-availability",
    ),
    path("consultations/", views.ConsultationListCreateView.as_view(), name="consultations"),
    path(
        "consultations/<uuid:consultation_id>/",
        views.ConsultationDetailView.as_view(),
        name="consultation-detail",
    ),
    path(
        "consultations/<uuid:consultation_id>/reschedule/",
        views.ConsultationRescheduleView.as_view(),
        name="consultation-reschedule",
    ),
    path(
        "consultations/<uuid:consultation_id>/cancel/",
        views.ConsultationCancelView.as_view(),
        name="consultation-cancel",
    ),
    path(
        "consultations/<uuid:consultation_id>/join/",
        views.ConsultationJoinView.as_view(),
        name="consultation-join",
    ),
    path(
        "consultations/<uuid:consultation_id>/leave/",
        views.ConsultationLeaveView.as_view(),
        name="consultation-leave",
    ),
]
