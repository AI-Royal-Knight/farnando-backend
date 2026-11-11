from __future__ import annotations

from django.urls import path

from apps.patients import views

app_name = "patients"

urlpatterns = [
    path("patients/me/", views.PatientMeView.as_view(), name="me"),
    path("patients/me/care-team/", views.CareTeamView.as_view(), name="care-team"),
    path("patients/me/overview/", views.OverviewView.as_view(), name="overview"),
    path("addresses/", views.AddressListCreateView.as_view(), name="addresses"),
    path("addresses/<uuid:address_id>/", views.AddressDetailView.as_view(), name="address"),
    path(
        "addresses/<uuid:address_id>/default/",
        views.AddressDefaultView.as_view(),
        name="address-default",
    ),
    path("geo/ubigeo/", views.UbigeoView.as_view(), name="ubigeo"),
]
