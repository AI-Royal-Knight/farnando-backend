from __future__ import annotations

from django.urls import path

from apps.clinical import views, views_profile

app_name = "clinical"

E = "clinical/evaluations/<uuid:evaluation_id>"
C = "clinical/consultations/<uuid:consultation_id>"
T = "clinical/treatments/<uuid:treatment_id>"

urlpatterns = [
    # Section 2
    path(
        "public/physician-invitations/<str:token>/",
        views_profile.InvitationDetailView.as_view(),
        name="invitation",
    ),
    path(
        "public/physician-invitations/<str:token>/accept/",
        views_profile.InvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
    path("public/specialties/", views_profile.SpecialtyListView.as_view(), name="specialties"),
    path("avatar-uploads/", views_profile.AvatarUploadCreateView.as_view(), name="avatar-uploads"),
    path(
        "avatar-uploads/<uuid:upload_id>/confirm/",
        views_profile.AvatarUploadConfirmView.as_view(),
        name="avatar-upload-confirm",
    ),
    path("physicians/me/", views_profile.PhysicianMeView.as_view(), name="physician-me"),
    # Section 3 and 7
    path("physicians/me/dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("physicians/me/calendar/", views.CalendarView.as_view(), name="calendar"),
    path(
        "physicians/me/availability-rules/",
        views.AvailabilityRuleListView.as_view(),
        name="availability-rules",
    ),
    path(
        "physicians/me/availability-rules/<uuid:rule_id>/",
        views.AvailabilityRuleDetailView.as_view(),
        name="availability-rule",
    ),
    path("physicians/me/time-blocks/", views.TimeBlockListView.as_view(), name="time-blocks"),
    path(
        "physicians/me/time-blocks/<uuid:block_id>/",
        views.TimeBlockDetailView.as_view(),
        name="time-block",
    ),
    # Section 4
    path("clinical/evaluations/", views.EvaluationQueueView.as_view(), name="evaluations"),
    path(f"{E}/", views.EvaluationPacketView.as_view(), name="evaluation"),
    path(f"{E}/claim/", views.EvaluationClaimView.as_view(), name="evaluation-claim"),
    path(f"{E}/release/", views.EvaluationReleaseView.as_view(), name="evaluation-release"),
    path(f"{E}/request-labs/", views.RequestLabsView.as_view(), name="evaluation-request-labs"),
    path(f"{E}/reject/", views.RejectView.as_view(), name="evaluation-reject"),
    path(f"{E}/approve/", views.ApproveView.as_view(), name="evaluation-approve"),
    path("clinical/lab-panels/", views.LabPanelCatalogView.as_view(), name="lab-panels"),
    path("clinical/products/", views.ProductListView.as_view(), name="products"),
    path(
        "prescription-documents/",
        views.PrescriptionDocumentCreateView.as_view(),
        name="prescription-documents",
    ),
    path(
        "prescription-documents/<uuid:document_id>/confirm/",
        views.PrescriptionDocumentConfirmView.as_view(),
        name="prescription-document-confirm",
    ),
    # Section 5
    path("clinical/patients/", views.PatientListView.as_view(), name="patients"),
    path("clinical/patients/<uuid:patient_id>/", views.PatientChartView.as_view(), name="patient"),
    path(
        "clinical/patients/<uuid:patient_id>/notes/",
        views.PatientNotesView.as_view(),
        name="patient-notes",
    ),
    path(f"{T}/adjust/", views.TreatmentAdjustView.as_view(), name="treatment-adjust"),
    path(
        f"{T}/pause/",
        views.TreatmentStatusView.as_view(action_name="pause"),
        name="treatment-pause",
    ),
    path(
        f"{T}/resume/",
        views.TreatmentStatusView.as_view(action_name="resume"),
        name="treatment-resume",
    ),
    path(
        f"{T}/discontinue/",
        views.TreatmentStatusView.as_view(action_name="discontinue"),
        name="treatment-discontinue",
    ),
    path("clinical/renewal-requests/", views.RenewalListView.as_view(), name="renewals"),
    path(
        "clinical/renewal-requests/<uuid:renewal_id>/approve/",
        views.RenewalApproveView.as_view(),
        name="renewal-approve",
    ),
    path(
        "clinical/renewal-requests/<uuid:renewal_id>/deny/",
        views.RenewalDenyView.as_view(),
        name="renewal-deny",
    ),
    # Section 6
    path(
        "clinical/consultations/",
        views.ClinicianConsultationListView.as_view(),
        name="consultations",
    ),
    path(f"{C}/", views.ClinicianConsultationDetailView.as_view(), name="consultation"),
    path(f"{C}/accept/", views.ConsultationAcceptView.as_view(), name="consultation-accept"),
    path(f"{C}/decline/", views.ConsultationDeclineView.as_view(), name="consultation-decline"),
    path(
        f"{C}/reschedule/",
        views.ConsultationPhysicianRescheduleView.as_view(),
        name="consultation-reschedule",
    ),
    path(
        f"{C}/cancel/", views.ConsultationPhysicianCancelView.as_view(), name="consultation-cancel"
    ),
    path(f"{C}/join/", views.ConsultationPhysicianJoinView.as_view(), name="consultation-join"),
    path(f"{C}/complete/", views.ConsultationCompleteView.as_view(), name="consultation-complete"),
    path(f"{C}/no-show/", views.ConsultationNoShowView.as_view(), name="consultation-no-show"),
]
