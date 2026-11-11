from __future__ import annotations

from django.urls import path

from apps.assessments import views

app_name = "assessments"

urlpatterns = [
    path("programs/", views.ProgramListView.as_view(), name="programs"),
    path("assessments/", views.AssessmentListCreateView.as_view(), name="assessments"),
    path(
        "assessments/<uuid:assessment_id>/",
        views.AssessmentDetailView.as_view(),
        name="assessment-detail",
    ),
    path(
        "assessments/<uuid:assessment_id>/questionnaire/",
        views.AssessmentQuestionnaireView.as_view(),
        name="assessment-questionnaire",
    ),
    path(
        "assessments/<uuid:assessment_id>/measurements/",
        views.AssessmentMeasurementsView.as_view(),
        name="assessment-measurements",
    ),
    path(
        "assessments/<uuid:assessment_id>/steps/<str:step_key>/",
        views.AssessmentStepView.as_view(),
        name="assessment-step",
    ),
    path(
        "assessments/<uuid:assessment_id>/summary/",
        views.AssessmentSummaryView.as_view(),
        name="assessment-summary",
    ),
    path(
        "assessments/<uuid:assessment_id>/submit/",
        views.AssessmentSubmitView.as_view(),
        name="assessment-submit",
    ),
    path(
        "assessments/<uuid:assessment_id>/request-review/",
        views.AssessmentRequestReviewView.as_view(),
        name="assessment-request-review",
    ),
    path(
        "assessments/<uuid:assessment_id>/cancel/",
        views.AssessmentCancelView.as_view(),
        name="assessment-cancel",
    ),
    path(
        "assessments/<uuid:assessment_id>/fee/",
        views.AssessmentFeeView.as_view(),
        name="assessment-fee",
    ),
    path(
        "assessments/<uuid:assessment_id>/fee/pay/",
        views.AssessmentFeePayView.as_view(),
        name="assessment-fee-pay",
    ),
    path(
        "assessments/<uuid:assessment_id>/lab-recommendations/",
        views.LabRecommendationsView.as_view(),
        name="assessment-lab-recommendations",
    ),
    path(
        "assessments/<uuid:assessment_id>/labs/submit/",
        views.AssessmentLabsSubmitView.as_view(),
        name="assessment-labs-submit",
    ),
    path(
        "assessments/<uuid:assessment_id>/labs/skip/",
        views.AssessmentLabsSkipView.as_view(),
        name="assessment-labs-skip",
    ),
    path(
        "assessments/<uuid:assessment_id>/evaluation/",
        views.AssessmentEvaluationView.as_view(),
        name="assessment-evaluation",
    ),
    path(
        "assessments/<uuid:assessment_id>/evaluation/prescription/download/",
        views.PrescriptionDownloadView.as_view(),
        name="assessment-prescription-download",
    ),
    path(
        "lab-uploads/",
        views.LabUploadListCreateView.as_view(),
        name="lab-uploads",
    ),
    path(
        "lab-uploads/<uuid:upload_id>/",
        views.LabUploadDetailView.as_view(),
        name="lab-upload-detail",
    ),
    path(
        "lab-uploads/<uuid:upload_id>/confirm/",
        views.LabUploadConfirmView.as_view(),
        name="lab-upload-confirm",
    ),
    path(
        "lab-uploads/<uuid:upload_id>/download/",
        views.LabUploadDownloadView.as_view(),
        name="lab-upload-download",
    ),
]
