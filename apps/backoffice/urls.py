from __future__ import annotations

from typing import Any

from django.urls import path

from apps.backoffice import views as v
from apps.backoffice.reference import RESOURCES

app_name = "backoffice"


def _reference_patterns() -> list[Any]:
    patterns = []
    for resource in RESOURCES:
        list_view, detail_view = v.reference_views(resource)
        patterns += [
            path(f"admin/{resource}/", list_view.as_view(), name=f"{resource}"),
            path(f"admin/{resource}/<uuid:pk>/", detail_view.as_view(), name=f"{resource}-detail"),
        ]
    return patterns


urlpatterns = [
    # Staff (2.1)
    path("admin/staff/", v.AdminStaffListView.as_view(), name="staff"),
    path("admin/staff/<uuid:member_id>/", v.AdminStaffDetailView.as_view(), name="staff-detail"),
    path(
        "admin/staff/<uuid:member_id>/suspend/",
        v.AdminStaffSuspendView.as_view(),
        name="staff-suspend",
    ),
    path(
        "admin/staff/<uuid:member_id>/reactivate/",
        v.AdminStaffReactivateView.as_view(),
        name="staff-reactivate",
    ),
    path(
        "admin/staff/<uuid:member_id>/resend-invitation/",
        v.AdminStaffResendView.as_view(),
        name="staff-resend",
    ),
    # Physicians (2.3)
    path("admin/physicians/", v.AdminPhysicianListView.as_view(), name="physicians"),
    path(
        "admin/physicians/<uuid:physician_id>/",
        v.AdminPhysicianDetailView.as_view(),
        name="physician",
    ),
    path(
        "admin/physicians/<uuid:physician_id>/verify/",
        v.AdminPhysicianVerifyView.as_view(),
        name="physician-verify",
    ),
    path(
        "admin/physicians/<uuid:physician_id>/reject/",
        v.AdminPhysicianRejectView.as_view(),
        name="physician-reject",
    ),
    path(
        "admin/physicians/<uuid:physician_id>/suspend/",
        v.AdminPhysicianSuspendView.as_view(),
        name="physician-suspend",
    ),
    path(
        "admin/physicians/<uuid:physician_id>/reactivate/",
        v.AdminPhysicianReactivateView.as_view(),
        name="physician-reactivate",
    ),
    path(
        "admin/physicians/<uuid:physician_id>/credentials/",
        v.AdminPhysicianCredentialsView.as_view(),
        name="physician-credentials",
    ),
    path(
        "admin/physician-invitations/",
        v.AdminPhysicianInvitationListView.as_view(),
        name="physician-invitations",
    ),
    path(
        "admin/physician-invitations/<uuid:invitation_id>/resend/",
        v.AdminPhysicianInvitationResendView.as_view(),
        name="physician-invitation-resend",
    ),
    path(
        "admin/physician-invitations/<uuid:invitation_id>/revoke/",
        v.AdminPhysicianInvitationRevokeView.as_view(),
        name="physician-invitation-revoke",
    ),
    # Dashboard (3.2)
    path("admin/dashboard/", v.AdminDashboardView.as_view(), name="dashboard"),
    # Products (8.1)
    path("admin/products/", v.AdminProductListView.as_view(), name="products"),
    path("admin/products/<uuid:product_id>/", v.AdminProductDetailView.as_view(), name="product"),
    path(
        "admin/products/<uuid:product_id>/activate/",
        v.AdminProductActiveView.as_view(),
        name="product-activate",
    ),
    path(
        "admin/products/<uuid:product_id>/deactivate/",
        v.AdminProductDeactivateView.as_view(),
        name="product-deactivate",
    ),
    # Programs (8.2, 8.5)
    path("admin/programs/", v.AdminProgramListView.as_view(), name="programs"),
    path("admin/programs/<uuid:program_id>/", v.AdminProgramDetailView.as_view(), name="program"),
    path(
        "admin/programs/<uuid:program_id>/activate/",
        v.AdminProgramActiveView.as_view(),
        name="program-activate",
    ),
    path(
        "admin/programs/<uuid:program_id>/deactivate/",
        v.AdminProgramDeactivateView.as_view(),
        name="program-deactivate",
    ),
    path(
        "admin/programs/<uuid:program_id>/evaluation-fee/",
        v.AdminProgramFeeView.as_view(),
        name="program-fee",
    ),
    path(
        "admin/programs/<uuid:program_id>/packages/",
        v.AdminProgramPackagesView.as_view(),
        name="program-packages",
    ),
    path(
        "admin/programs/<uuid:program_id>/packages/reorder/",
        v.AdminProgramPackagesReorderView.as_view(),
        name="program-packages-reorder",
    ),
    # Packages (8.3)
    path("admin/packages/", v.AdminPackageCreateView.as_view(), name="packages"),
    path("admin/packages/<uuid:package_id>/", v.AdminPackageDetailView.as_view(), name="package"),
    path(
        "admin/packages/<uuid:package_id>/activate/",
        v.AdminPackageActiveView.as_view(),
        name="package-activate",
    ),
    path(
        "admin/packages/<uuid:package_id>/deactivate/",
        v.AdminPackageDeactivateView.as_view(),
        name="package-deactivate",
    ),
    # Questionnaires (8.4)
    path("admin/questionnaires/", v.AdminQuestionnaireListView.as_view(), name="questionnaires"),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/",
        v.AdminQuestionnaireDetailView.as_view(),
        name="questionnaire",
    ),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/new-draft/",
        v.AdminQuestionnaireNewDraftView.as_view(),
        name="questionnaire-new-draft",
    ),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/steps/",
        v.AdminQuestionnaireStepsView.as_view(),
        name="questionnaire-steps",
    ),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/reorder/",
        v.AdminQuestionnaireReorderView.as_view(),
        name="questionnaire-reorder",
    ),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/validate/",
        v.AdminQuestionnaireValidateView.as_view(),
        name="questionnaire-validate",
    ),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/publish/",
        v.AdminQuestionnairePublishView.as_view(),
        name="questionnaire-publish",
    ),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/activate/",
        v.AdminQuestionnaireActiveView.as_view(),
        name="questionnaire-activate",
    ),
    path(
        "admin/questionnaires/<uuid:questionnaire_id>/deactivate/",
        v.AdminQuestionnaireDeactivateView.as_view(),
        name="questionnaire-deactivate",
    ),
    path(
        "admin/questionnaire-steps/<uuid:step_id>/",
        v.AdminQuestionnaireStepDetailView.as_view(),
        name="questionnaire-step",
    ),
    path(
        "admin/questionnaire-steps/<uuid:step_id>/questions/",
        v.AdminStepQuestionsView.as_view(),
        name="step-questions",
    ),
    path(
        "admin/questions/<uuid:question_id>/", v.AdminQuestionDetailView.as_view(), name="question"
    ),
    # Content (9.1, B1) and uploads (9.4)
    path("admin/content-items/", v.AdminContentListView.as_view(), name="content-items"),
    path("admin/content-items/<uuid:item_id>/", v.AdminContentDetailView.as_view(), name="content"),
    path(
        "admin/content-items/<uuid:item_id>/publish/",
        v.AdminContentPublishView.as_view(),
        name="content-publish",
    ),
    path(
        "admin/content-items/<uuid:item_id>/unpublish/",
        v.AdminContentUnpublishView.as_view(),
        name="content-unpublish",
    ),
    path(
        "admin/content-items/<uuid:item_id>/preview/",
        v.AdminContentPreviewView.as_view(),
        name="content-preview",
    ),
    path("public/content-items/", v.PublicContentItemsView.as_view(), name="public-content"),
    path("admin/asset-uploads/", v.AdminAssetUploadView.as_view(), name="asset-uploads"),
    path(
        "admin/asset-uploads/<uuid:asset_id>/confirm/",
        v.AdminAssetConfirmView.as_view(),
        name="asset-confirm",
    ),
    # Reference data (9.2) and legal documents (9.3)
    *_reference_patterns(),
    path("admin/legal-documents/", v.AdminLegalListView.as_view(), name="legal-documents"),
    path(
        "admin/legal-documents/<uuid:document_id>/",
        v.AdminLegalDetailView.as_view(),
        name="legal-document",
    ),
    path(
        "admin/legal-documents/<uuid:document_id>/publish/",
        v.AdminLegalPublishView.as_view(),
        name="legal-document-publish",
    ),
    # Settings (9.5)
    path("admin/settings/<str:group>/", v.AdminSettingsView.as_view(), name="settings"),
    # Reports (10) and audit (11.1)
    path("admin/reports/overview/", v.AdminReportOverviewView.as_view(), name="report-overview"),
    path("admin/reports/revenue/", v.AdminReportRevenueView.as_view(), name="report-revenue"),
    path("admin/reports/exports/", v.AdminReportExportView.as_view(), name="report-exports"),
    path(
        "admin/reports/exports/<uuid:export_id>/",
        v.AdminReportExportDetailView.as_view(),
        name="report-export",
    ),
    path("admin/audit-logs/", v.AdminAuditLogView.as_view(), name="audit-logs"),
    # Privacy (11.2)
    path(
        "patients/me/privacy-requests/",
        v.PatientPrivacyRequestView.as_view(),
        name="patient-privacy-requests",
    ),
    path("admin/privacy-requests/", v.AdminPrivacyListView.as_view(), name="privacy-requests"),
    path(
        "admin/privacy-requests/<uuid:request_id>/",
        v.AdminPrivacyDetailView.as_view(),
        name="privacy-request",
    ),
    path(
        "admin/privacy-requests/<uuid:request_id>/status/",
        v.AdminPrivacyStatusView.as_view(),
        name="privacy-request-status",
    ),
    path(
        "admin/privacy-requests/<uuid:request_id>/fulfill/",
        v.AdminPrivacyFulfillView.as_view(),
        name="privacy-request-fulfill",
    ),
]
