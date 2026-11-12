from __future__ import annotations

from django.urls import path

from apps.support import views, views_tickets

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
    path("support-tickets/", views_tickets.PatientTicketListView.as_view(), name="tickets"),
    path(
        "support-tickets/<uuid:ticket_id>/",
        views_tickets.PatientTicketDetailView.as_view(),
        name="ticket-detail",
    ),
    path(
        "support-tickets/<uuid:ticket_id>/messages/",
        views_tickets.PatientTicketMessageView.as_view(),
        name="ticket-messages",
    ),
    path(
        "support-tickets/<uuid:ticket_id>/close/",
        views_tickets.PatientTicketCloseView.as_view(),
        name="ticket-close",
    ),
    path(
        "support-ticket-categories/",
        views_tickets.TicketCategoryListView.as_view(),
        name="ticket-categories",
    ),
]
