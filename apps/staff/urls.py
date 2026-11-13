from __future__ import annotations

from django.urls import path

from apps.staff import views as v

app_name = "staff"

urlpatterns = [
    path(
        "public/staff-invitations/<str:token>/",
        v.StaffInvitationDetailView.as_view(),
        name="invitation",
    ),
    path(
        "public/staff-invitations/<str:token>/accept/",
        v.StaffInvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
    path("staff/dashboard/", v.StaffDashboardView.as_view(), name="dashboard"),
    # Patients (4)
    path("staff/patients/", v.StaffPatientListView.as_view(), name="patients"),
    path("staff/patients/<uuid:patient_id>/", v.StaffPatientDetailView.as_view(), name="patient"),
    path(
        "staff/patients/<uuid:patient_id>/assign-physician/",
        v.StaffPatientAssignView.as_view(),
        name="patient-assign",
    ),
    path(
        "staff/patients/<uuid:patient_id>/suspend/",
        v.StaffPatientSuspendView.as_view(),
        name="patient-suspend",
    ),
    path(
        "staff/patients/<uuid:patient_id>/reactivate/",
        v.StaffPatientReactivateView.as_view(),
        name="patient-reactivate",
    ),
    # Evaluations (5)
    path("staff/physicians/", v.StaffPhysicianListView.as_view(), name="physicians"),
    path("staff/evaluations/", v.StaffEvaluationListView.as_view(), name="evaluations"),
    path(
        "staff/evaluations/<uuid:evaluation_id>/",
        v.StaffEvaluationDetailView.as_view(),
        name="evaluation",
    ),
    path(
        "staff/evaluations/<uuid:evaluation_id>/recommended-physicians/",
        v.StaffEvaluationRecommendedView.as_view(),
        name="evaluation-recommended",
    ),
    path(
        "staff/evaluations/<uuid:evaluation_id>/assign/",
        v.StaffEvaluationAssignView.as_view(),
        name="evaluation-assign",
    ),
    path(
        "staff/evaluations/<uuid:evaluation_id>/unassign/",
        v.StaffEvaluationUnassignView.as_view(),
        name="evaluation-unassign",
    ),
    path(
        "staff/evaluations/<uuid:evaluation_id>/priority/",
        v.StaffEvaluationPriorityView.as_view(),
        name="evaluation-priority",
    ),
    path(
        "staff/evaluations/<uuid:evaluation_id>/request-labs/",
        v.StaffEvaluationRequestLabsView.as_view(),
        name="evaluation-request-labs",
    ),
    path(
        "staff/evaluations/<uuid:evaluation_id>/reopen/",
        v.StaffEvaluationReopenView.as_view(),
        name="evaluation-reopen",
    ),
    # Tickets (6.3)
    path("staff/tickets/", v.StaffTicketListView.as_view(), name="tickets"),
    path("staff/tickets/<uuid:ticket_id>/", v.StaffTicketDetailView.as_view(), name="ticket"),
    path(
        "staff/tickets/<uuid:ticket_id>/assign/",
        v.StaffTicketAssignView.as_view(),
        name="ticket-assign",
    ),
    path(
        "staff/tickets/<uuid:ticket_id>/reply/",
        v.StaffTicketReplyView.as_view(),
        name="ticket-reply",
    ),
    path(
        "staff/tickets/<uuid:ticket_id>/status/",
        v.StaffTicketStatusView.as_view(),
        name="ticket-status",
    ),
    path(
        "staff/tickets/<uuid:ticket_id>/priority/",
        v.StaffTicketPriorityView.as_view(),
        name="ticket-priority",
    ),
    # Complaints (6.4)
    path("staff/complaints/", v.StaffComplaintListView.as_view(), name="complaints"),
    path(
        "staff/complaints/<str:complaint_number>/",
        v.StaffComplaintDetailView.as_view(),
        name="complaint",
    ),
    path(
        "staff/complaints/<str:complaint_number>/respond/",
        v.StaffComplaintRespondView.as_view(),
        name="complaint-respond",
    ),
    path(
        "staff/complaints/<str:complaint_number>/status/",
        v.StaffComplaintStatusView.as_view(),
        name="complaint-status",
    ),
    # Orders (7.1)
    path("staff/orders/", v.StaffOrderListView.as_view(), name="orders"),
    path("staff/orders/<uuid:order_id>/", v.StaffOrderDetailView.as_view(), name="order"),
    path(
        "staff/orders/<uuid:order_id>/transition/",
        v.StaffOrderTransitionView.as_view(),
        name="order-transition",
    ),
    path(
        "staff/orders/<uuid:order_id>/tracking/",
        v.StaffOrderTrackingView.as_view(),
        name="order-tracking",
    ),
    path("staff/orders/<uuid:order_id>/notes/", v.StaffOrderNoteView.as_view(), name="order-notes"),
    path(
        "staff/orders/<uuid:order_id>/cancel/",
        v.StaffOrderCancelView.as_view(),
        name="order-cancel",
    ),
    # Subscriptions (7.2)
    path("staff/subscriptions/", v.StaffSubscriptionListView.as_view(), name="subscriptions"),
    path(
        "staff/subscriptions/<uuid:subscription_id>/",
        v.StaffSubscriptionDetailView.as_view(),
        name="subscription",
    ),
    path(
        "staff/subscriptions/<uuid:subscription_id>/pause/",
        v.StaffSubscriptionPauseView.as_view(),
        name="subscription-pause",
    ),
    path(
        "staff/subscriptions/<uuid:subscription_id>/resume/",
        v.StaffSubscriptionResumeView.as_view(),
        name="subscription-resume",
    ),
    path(
        "staff/subscriptions/<uuid:subscription_id>/cancel/",
        v.StaffSubscriptionCancelView.as_view(),
        name="subscription-cancel",
    ),
    path(
        "staff/subscriptions/<uuid:subscription_id>/new-refill/",
        v.StaffSubscriptionRefillView.as_view(),
        name="subscription-refill",
    ),
    # Refunds (7.3)
    path("staff/payments/<uuid:payment_id>/refund/", v.StaffRefundView.as_view(), name="refund"),
]
