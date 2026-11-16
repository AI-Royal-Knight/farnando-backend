"""Part 2 program, assessment, fee, lab and evaluation endpoints."""

from __future__ import annotations

from typing import Any, cast

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified
from apps.accounts.serializers import EmptyBodySerializer
from apps.assessments import labs, selectors, services
from apps.assessments.enums import AssessmentStatus, EvaluationStatus, PaymentMethod
from apps.assessments.models import Assessment, Evaluation, Program
from apps.assessments.payments import PaymentService
from apps.assessments.permissions import (
    IsClinicalReader,
    IsPatientOrAdmin,
    IsPatientPhysicianAdmin,
    IsPatientStaffAdmin,
)
from apps.assessments.serializers import (
    AssessmentCreateSerializer,
    AssessmentDetailSerializer,
    AssessmentSummarySerializer,
    CancelAssessmentSerializer,
    DownloadUrlSerializer,
    EvaluationSerializer,
    FeeSerializer,
    LabRecommendationsSerializer,
    LabsSkipResponseSerializer,
    LabsSubmitResponseSerializer,
    LabUploadCreateSerializer,
    LabUploadInitSerializer,
    LabUploadSerializer,
    MeasurementInputSerializer,
    MeasurementsSerializer,
    PaymentCreateSerializer,
    PaymentSerializer,
    ProgramSerializer,
    QuestionnaireSerializer,
    RequestReviewResponseSerializer,
    ReviewRequestInputSerializer,
    StepAnswersInputSerializer,
    StepAnswersSerializer,
    SubmitAssessmentResponseSerializer,
    SummarySerializer,
)
from apps.assessments.throttles import AssessmentPaymentThrottle, MethodScopedRateThrottle
from apps.core.audit import record_audit
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.permissions import IsPatient
from apps.core.schema import COMMON_ERRORS


def _audit_non_owner_read(request: Request, assessment: Assessment, action: str) -> None:
    if request.user.pk != assessment.user_id:
        record_audit(
            actor=request.user,
            action=action,
            object_type="Assessment",
            object_id=assessment.pk,
            request=request,
            metadata={"program_id": str(assessment.program_id)},
        )


def _evaluation_for(assessment: Assessment) -> Evaluation:
    evaluation = Evaluation.objects.filter(assessment=assessment).first()
    if evaluation is None:
        raise NotFound()
    return evaluation


class ProgramListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["assessments"],
        summary="Programs a patient can be assessed for",
        responses={200: ProgramSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = Program.objects.filter(is_active=True).order_by("position", "name")
        return Response(ProgramSerializer(queryset, many=True).data)


class AssessmentListCreateView(APIView):
    permission_classes = [IsClinicalReader]
    throttle_classes = [MethodScopedRateThrottle]
    throttle_scopes = {"POST": "assessment_create"}

    @extend_schema(
        tags=["assessments"],
        summary="List the patient's assessments",
        responses={200: AssessmentSummarySerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = selectors.assessments_visible_to(request.user)
        status_filter = request.query_params.get("status")
        if status_filter is not None:
            if status_filter not in AssessmentStatus.values:
                raise ValidationError({"status": ["Unknown assessment status."]})
            queryset = queryset.filter(status=status_filter)
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AssessmentSummarySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(
        tags=["assessments"],
        summary="Start or resume an assessment",
        description="Requires Idempotency-Key. Existing drafts are returned with 200.",
        request=AssessmentCreateSerializer,
        responses={
            200: AssessmentDetailSerializer,
            201: AssessmentDetailSerializer,
            **COMMON_ERRORS,
        },
    )
    def post(self, request: Request) -> Response:
        if getattr(request.user, "role", None) != "PATIENT":
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        serializer = AssessmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = cast(Any, request.user)

        def create() -> tuple[int, dict[str, Any]]:
            assessment, created = services.create_or_resume_assessment(
                user=user, program_id=serializer.validated_data["program_id"]
            )
            return (
                status.HTTP_201_CREATED if created else status.HTTP_200_OK,
                dict(AssessmentDetailSerializer(assessment).data),
            )

        from apps.core.idempotency import execute

        result = execute(
            key=request.headers.get("Idempotency-Key"),
            endpoint="assessments.create",
            payload=serializer.validated_data,
            scope=str(user.pk),
            callback=create,
        )
        return Response(result.body, status=result.status_code)


class AssessmentDetailView(APIView):
    permission_classes = [IsClinicalReader]

    @extend_schema(
        tags=["assessments"],
        summary="Assessment detail (resume, details and overview)",
        responses={200: AssessmentDetailSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_assessment_for_read(
            user=request.user, assessment_id=assessment_id
        )
        _audit_non_owner_read(request, assessment, "assessment.read")
        return Response(AssessmentDetailSerializer(assessment).data)


class AssessmentQuestionnaireView(APIView):
    permission_classes = [IsPatientOrAdmin]

    @extend_schema(
        tags=["assessments"],
        summary="Questions for the pinned questionnaire version",
        responses={200: QuestionnaireSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_assessment_for_read(
            user=request.user, assessment_id=assessment_id
        )
        _audit_non_owner_read(request, assessment, "assessment.questionnaire_read")
        from apps.assessments.models import QuestionnaireVersion

        questionnaire = (
            QuestionnaireVersion.objects.select_related("program")
            .prefetch_related("steps__questions__options")
            .get(pk=assessment.questionnaire_version_id)
        )
        return Response(QuestionnaireSerializer(questionnaire).data)


class AssessmentMeasurementsView(APIView):
    permission_classes = [IsPatient]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "assessment_measurements"

    @extend_schema(
        tags=["assessments"],
        summary="Save the Body Measurements step",
        request=MeasurementInputSerializer,
        responses={200: MeasurementsSerializer, **COMMON_ERRORS},
    )
    def put(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        serializer = MeasurementInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        measurement = services.save_measurements(assessment, serializer.validated_data)
        return Response(MeasurementsSerializer(measurement).data)


class AssessmentStepView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["assessments"],
        summary="Save one questionnaire step",
        request=StepAnswersInputSerializer,
        responses={200: StepAnswersSerializer, **COMMON_ERRORS},
    )
    def put(self, request: Request, assessment_id: str, step_key: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        serializer = StepAnswersInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step, _accepted, _complete = services.save_step(
            assessment, step_key, serializer.validated_data["answers"]
        )
        return Response(services.step_answers_payload(assessment, step))


class AssessmentSummaryView(APIView):
    permission_classes = [IsPatientOrAdmin]

    @extend_schema(
        tags=["assessments"],
        summary="Personalized health summary",
        responses={200: SummarySerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_assessment_for_read(
            user=request.user, assessment_id=assessment_id
        )
        _audit_non_owner_read(request, assessment, "assessment.summary_read")
        return Response(SummarySerializer(services.summary_data(assessment)).data)


class AssessmentSubmitView(APIView):
    # Permissions run before throttles: an unverified email is refused without spending
    # one of the patient's hourly submit attempts.
    permission_classes = [IsPatient, IsEmailVerified]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "assessment_submit"

    @extend_schema(
        tags=["assessments"],
        summary="Finalize the questionnaire",
        description="Requires Idempotency-Key.",
        request=EmptyBodySerializer,
        responses={200: SubmitAssessmentResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        EmptyBodySerializer(data=request.data).is_valid(raise_exception=True)

        def submit() -> tuple[int, dict[str, Any]]:
            evaluation, next_screen = services.submit_assessment(assessment, request=request)
            body = {
                "assessment_id": str(evaluation.assessment_id),
                "evaluation_status": evaluation.status,
                "screening_result": evaluation.assessment.screening_result,
                "next": next_screen,
            }
            return status.HTTP_200_OK, body

        from apps.core.idempotency import execute

        result = execute(
            key=request.headers.get("Idempotency-Key"),
            endpoint="assessments.submit",
            payload={},
            scope=f"{request.user.pk}:{assessment.pk}",
            callback=submit,
        )
        return Response(result.body, status=result.status_code)


class AssessmentRequestReviewView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["evaluations"],
        summary="Ask a physician to review an automated decline",
        request=ReviewRequestInputSerializer,
        responses={200: RequestReviewResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        serializer = ReviewRequestInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_review(
            assessment,
            note=serializer.validated_data.get("note", ""),
            request=request,
        )
        return Response(
            {"evaluation_status": EvaluationStatus.SCREENED_OUT, "review_requested": True}
        )


class AssessmentCancelView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["evaluations"],
        summary="Cancel an assessment before a physician decision",
        request=CancelAssessmentSerializer,
        responses={200: AssessmentSummarySerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        serializer = CancelAssessmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.cancel_assessment(
            assessment,
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        return Response(AssessmentSummarySerializer(assessment).data)


class AssessmentFeeView(APIView):
    permission_classes = [IsPatientStaffAdmin]

    @extend_schema(
        tags=["evaluations"],
        summary="Evaluation fee and latest payment attempt",
        responses={200: FeeSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_assessment_for_read(
            user=request.user, assessment_id=assessment_id, clinical=False
        )
        _audit_non_owner_read(request, assessment, "evaluation.fee_read")
        evaluation = _evaluation_for(assessment)
        fee = getattr(evaluation, "fee", None)
        if fee is None:
            raise NotFound()
        return Response(FeeSerializer(fee).data)


class AssessmentFeePayView(APIView):
    permission_classes = [IsPatient]
    throttle_classes = [AssessmentPaymentThrottle]

    @extend_schema(
        tags=["evaluations"],
        summary="Create a card charge through the payment provider",
        description=(
            "Requires Idempotency-Key. The final state is set by the verified "
            "provider webhook; poll the fee endpoint until the status changes."
        ),
        request=PaymentCreateSerializer,
        responses={200: PaymentSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        evaluation = _evaluation_for(assessment)
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            from apps.core.exceptions import IdempotencyKeyRequired

            raise IdempotencyKeyRequired()

        def pay() -> tuple[int, dict[str, Any]]:
            payment = PaymentService.create(
                evaluation=evaluation,
                payment_method=serializer.validated_data["payment_method"],
                provider_token=serializer.validated_data["provider_token"],
                idempotency_key=idempotency_key,
            )
            return status.HTTP_200_OK, dict(PaymentSerializer(payment).data)

        from apps.core.idempotency import execute

        result = execute(
            key=idempotency_key,
            endpoint="evaluations.pay",
            payload=serializer.validated_data,
            scope=f"{request.user.pk}:{assessment.pk}",
            callback=pay,
        )
        return Response(result.body, status=result.status_code)


class AssessmentFeeDemoPayView(APIView):
    """Local/demo only: settle the fee without a provider charge.

    The portal's "Skip payment" button calls this so the labs and review flow can be
    exercised without a card. Disabled (404) unless ``DEMO_PAYMENTS_ENABLED`` is on.
    """

    permission_classes = [IsPatient]
    throttle_classes = [AssessmentPaymentThrottle]

    @extend_schema(
        tags=["evaluations"],
        summary="Settle the evaluation fee without a provider (demo only)",
        description=(
            "Disabled unless `DEMO_PAYMENTS_ENABLED` is on (local/demo). Marks the fee paid "
            "and moves the evaluation to review, exactly like a verified webhook."
        ),
        request=None,
        responses={200: PaymentSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, assessment_id: str) -> Response:
        if not bool(getattr(settings, "DEMO_PAYMENTS_ENABLED", False)):
            raise NotFound()
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        evaluation = _evaluation_for(assessment)
        payment = PaymentService.create_demo(
            evaluation=evaluation, payment_method=PaymentMethod.CULQI
        )
        return Response(dict(PaymentSerializer(payment).data), status=status.HTTP_200_OK)


class LabRecommendationsView(APIView):
    permission_classes = [IsPatientPhysicianAdmin]

    @extend_schema(
        tags=["labs"],
        summary="Recommended baseline lab panels",
        responses={200: LabRecommendationsSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_assessment_for_read(
            user=request.user, assessment_id=assessment_id
        )
        _audit_non_owner_read(request, assessment, "evaluation.labs_read")
        evaluation = _evaluation_for(assessment)
        return Response(LabRecommendationsSerializer(services.lab_recommendations(evaluation)).data)


class LabUploadListCreateView(APIView):
    permission_classes = [IsPatient]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "lab_upload"

    @extend_schema(
        tags=["labs"],
        summary="List lab uploads for an assessment",
        responses={200: LabUploadSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        raw_assessment_id = request.query_params.get("assessment_id")
        if not raw_assessment_id:
            raise ValidationError({"assessment_id": ["This query parameter is required."]})
        assessment = selectors.get_own_assessment(
            user=request.user, assessment_id=raw_assessment_id
        )
        queryset = (
            assessment.lab_uploads.filter(is_deleted=False)
            .select_related("assessment")
            .order_by("-created_at")
        )
        return Response(LabUploadSerializer(queryset, many=True).data)

    @extend_schema(
        tags=["labs"],
        summary="Start a pre-signed lab upload",
        request=LabUploadCreateSerializer,
        responses={201: LabUploadInitSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = LabUploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assessment = selectors.get_own_assessment(
            user=request.user, assessment_id=serializer.validated_data["assessment_id"]
        )
        upload, target = labs.start_lab_upload(
            assessment=assessment, data=serializer.validated_data, request=request
        )
        data = LabUploadInitSerializer(upload, context={"upload": target}).data
        return Response(data, status=status.HTTP_201_CREATED)


class LabUploadDetailView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["labs"],
        summary="Lab upload detail",
        responses={200: LabUploadSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, upload_id: str) -> Response:
        upload = selectors.get_own_lab_upload(user=request.user, upload_id=upload_id)
        return Response(LabUploadSerializer(upload).data)

    @extend_schema(
        tags=["labs"],
        summary="Delete a lab upload",
        responses={204: None, **COMMON_ERRORS},
    )
    def delete(self, request: Request, upload_id: str) -> Response:
        upload = selectors.get_own_lab_upload(user=request.user, upload_id=upload_id)
        labs.delete_lab_upload(upload=upload, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LabUploadConfirmView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["labs"],
        summary="Confirm a browser upload and queue the antivirus scan",
        request=EmptyBodySerializer,
        responses={200: LabUploadSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, upload_id: str) -> Response:
        upload = selectors.get_own_lab_upload(user=request.user, upload_id=upload_id)
        EmptyBodySerializer(data=request.data).is_valid(raise_exception=True)
        upload = labs.confirm_lab_upload(upload=upload, request=request)
        return Response(LabUploadSerializer(upload).data)


class LabUploadDownloadView(APIView):
    permission_classes = [IsPatientPhysicianAdmin]

    @extend_schema(
        tags=["labs"],
        summary="Pre-signed download for a clean lab file",
        responses={200: DownloadUrlSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, upload_id: str) -> Response:
        upload = selectors.get_lab_upload_for_download(user=request.user, upload_id=upload_id)
        return Response(labs.download_lab_upload(upload=upload, request=request))


class AssessmentLabsSubmitView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["labs"],
        summary="Submit uploaded blood tests",
        request=EmptyBodySerializer,
        responses={200: LabsSubmitResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        EmptyBodySerializer(data=request.data).is_valid(raise_exception=True)
        return Response(labs.submit_labs(assessment=assessment, request=request))


class AssessmentLabsSkipView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["labs"],
        summary="Defer lab upload",
        request=EmptyBodySerializer,
        responses={200: LabsSkipResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_own_assessment(user=request.user, assessment_id=assessment_id)
        EmptyBodySerializer(data=request.data).is_valid(raise_exception=True)
        return Response(labs.skip_labs(assessment=assessment, request=request))


class AssessmentEvaluationView(APIView):
    permission_classes = [IsPatientStaffAdmin]

    @extend_schema(
        tags=["evaluations"],
        summary="Patient-facing evaluation status and checklist",
        responses={200: EvaluationSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_assessment_for_read(
            user=request.user, assessment_id=assessment_id
        )
        _audit_non_owner_read(request, assessment, "evaluation.read")
        if assessment.status == AssessmentStatus.DRAFT:
            raise NotFound()
        evaluation = _evaluation_for(assessment)
        return Response(EvaluationSerializer(evaluation).data)


class PrescriptionDownloadView(APIView):
    permission_classes = [IsPatientPhysicianAdmin]

    @extend_schema(
        tags=["evaluations"],
        summary="Pre-signed prescription PDF download",
        responses={200: DownloadUrlSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, assessment_id: str) -> Response:
        assessment = selectors.get_assessment_for_read(
            user=request.user, assessment_id=assessment_id
        )
        _audit_non_owner_read(request, assessment, "prescription.read")
        evaluation = _evaluation_for(assessment)
        return Response(labs.download_prescription(evaluation=evaluation, request=request))


__all__ = [
    "AssessmentCancelView",
    "AssessmentDetailView",
    "AssessmentEvaluationView",
    "AssessmentFeePayView",
    "AssessmentFeeView",
    "AssessmentLabsSkipView",
    "AssessmentLabsSubmitView",
    "AssessmentListCreateView",
    "AssessmentMeasurementsView",
    "AssessmentQuestionnaireView",
    "AssessmentRequestReviewView",
    "AssessmentStepView",
    "AssessmentSubmitView",
    "AssessmentSummaryView",
    "LabRecommendationsView",
    "LabUploadConfirmView",
    "LabUploadDetailView",
    "LabUploadDownloadView",
    "LabUploadListCreateView",
    "PrescriptionDownloadView",
    "ProgramListView",
]
