"""Part 4 sections 3-7: dashboard, review queue, patients, consultations, calendar."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, cast

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as drf
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.models import Evaluation
from apps.clinical import consultations as clinical_consultations
from apps.clinical import documents, evaluations
from apps.clinical import treatments as clinical_treatments
from apps.clinical.models import ClinicalNote
from apps.clinical.permissions import (
    IsPhysician,
    IsVerifiedPhysician,
    IsVerifiedPhysicianOrAdmin,
    physician_profile,
)
from apps.clinical.serializers import (
    PATIENT_LIST_STATUSES,
    AdjustSerializer,
    AgendaSerializer,
    ApproveResponseSerializer,
    ApproveSerializer,
    AvailabilityRuleSerializer,
    BiometricsSerializer,
    CalendarSerializer,
    ChartPatientSerializer,
    ClinicalEvaluationItemSerializer,
    ClinicianConsultationDetailSerializer,
    ClinicianConsultationItemSerializer,
    ClinicianTreatmentSerializer,
    CompleteSerializer,
    ConsultationListResponseSerializer,
    DashboardSerializer,
    DeclineSerializer,
    LabAttachmentSerializer,
    LabPanelCatalogSerializer,
    NoShowSerializer,
    NoteCreateSerializer,
    NoteSerializer,
    PatientListItemSerializer,
    PhysicianRescheduleSerializer,
    PrescriptionDocumentCreateSerializer,
    PrescriptionDocumentSerializer,
    PreviousEvaluationSerializer,
    ProductSerializer,
    QueueResponseSerializer,
    RejectSerializer,
    ReleaseReasonSerializer,
    RenewalApproveSerializer,
    RenewalDenySerializer,
    RenewalItemSerializer,
    RequestLabsSerializer,
    RequiredReasonSerializer,
    ReviewPacketSerializer,
    StatusChangeSerializer,
    TimeBlockSerializer,
)
from apps.clinical.serializers_profile import UploadInitSerializer
from apps.consultations.models import (
    AvailabilityBlock,
    AvailabilityRule,
    Consultation,
    ConsultationStatus,
)
from apps.consultations.serializers import JoinResponseSerializer
from apps.core.audit import record_audit
from apps.core.idempotency import execute
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.schema import COMMON_ERRORS, error_response
from apps.messaging.models import MessageThread
from apps.treatments.models import Product, RenewalRequestStatus

CONCURRENCY_ERRORS = {
    412: error_response("Stale If-Match version."),
    428: error_response("If-Match header is required."),
}
IF_MATCH = OpenApiParameter(
    "If-Match", OpenApiTypes.STR, location=OpenApiParameter.HEADER, required=True
)
IDEMPOTENCY = OpenApiParameter(
    "Idempotency-Key", OpenApiTypes.UUID, location=OpenApiParameter.HEADER, required=True
)


def _user(request: Request) -> User:
    return cast(User, request.user)


def _idempotent(
    request: Request, endpoint: str, payload: Any, scope: str, fn: Callable[[], dict[str, Any]]
) -> Response:
    def callback() -> tuple[int, dict[str, Any]]:
        return status.HTTP_200_OK, fn()

    result = execute(
        key=request.headers.get("Idempotency-Key"),
        endpoint=endpoint,
        payload=payload,
        scope=f"{request.user.pk}:{scope}",
        callback=callback,
    )
    response = Response(result.body, status=result.status_code)
    if isinstance(result.body, dict) and "version" in result.body:
        response["ETag"] = f'"{result.body["version"]}"'
    return response


def _item(evaluation: Evaluation) -> dict[str, Any]:
    refreshed = evaluations._base().get(pk=evaluation.pk)
    return dict(ClinicalEvaluationItemSerializer(evaluations.item_payload(refreshed)).data)


def _parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({field: ["Use YYYY-MM-DD."]}) from exc


def _uuid_or_400(value: str | None, field: str) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError({field: ["Must be a valid UUID."]}) from exc


# ---------------------------------------------------------------------------
# Section 3
# ---------------------------------------------------------------------------


class DashboardView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Physician dashboard",
        responses={200: DashboardSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return Response(DashboardSerializer(clinical_consultations.dashboard(_user(request))).data)


# ---------------------------------------------------------------------------
# Section 4
# ---------------------------------------------------------------------------


class EvaluationQueueView(APIView):
    permission_classes = [IsVerifiedPhysicianOrAdmin]

    @extend_schema(
        tags=["clinical"],
        operation_id="clinical_evaluations_list",
        summary="Evaluation review queue",
        parameters=[
            OpenApiParameter("scope", OpenApiTypes.STR, enum=["QUEUE", "MINE"]),
            OpenApiParameter(
                "status", OpenApiTypes.STR, enum=["SUBMITTED", "IN_REVIEW", "PENDING_LABS"]
            ),
            OpenApiParameter("program_id", OpenApiTypes.UUID),
            OpenApiParameter("labs", OpenApiTypes.STR, enum=["ATTACHED", "SCANNING", "MISSING"]),
            OpenApiParameter("ordering", OpenApiTypes.STR, enum=["submitted_at", "-submitted_at"]),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: QueueResponseSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        user = _user(request)
        params = request.query_params
        scope = params.get("scope", "QUEUE")
        if scope not in ("QUEUE", "MINE"):
            raise ValidationError({"scope": ["Must be QUEUE or MINE."]})
        if user.role == Role.ADMIN:
            queue = evaluations._base().filter(status="SUBMITTED", physician__isnull=True)
            mine = evaluations._base().none()
        else:
            profile = physician_profile(user)
            queue = evaluations.queue_for(user, profile)
            mine = evaluations.mine_for(user)
        counts = {"queue": queue.count(), "mine": mine.count()}
        counts["all"] = counts["queue"] + counts["mine"]
        queryset = queue if scope == "QUEUE" else mine
        status_filter = params.get("status")
        if status_filter:
            if status_filter not in ("SUBMITTED", "IN_REVIEW", "PENDING_LABS"):
                raise ValidationError({"status": ["Unknown status."]})
            queryset = queryset.filter(status=status_filter)
        program_id = _uuid_or_400(params.get("program_id"), "program_id")
        if program_id:
            queryset = queryset.filter(assessment__program_id=program_id)
        labs = params.get("labs")
        if labs:
            if labs not in ("ATTACHED", "SCANNING", "MISSING"):
                raise ValidationError({"labs": ["Unknown labs state."]})
            queryset = evaluations.filter_labs(queryset, labs)
        ordering = params.get("ordering", "submitted_at")
        if ordering not in ("submitted_at", "-submitted_at"):
            raise ValidationError({"ordering": ["Unknown ordering."]})
        queryset = queryset.order_by(ordering, "pk")
        if user.role == Role.ADMIN:
            record_audit(
                actor=user,
                action="evaluation.queue_read",
                object_type="Evaluation",
                object_id="*",
                request=request,
            )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        return Response(
            {
                "counts": counts,
                "count": paginator.page.paginator.count,  # type: ignore[union-attr]
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": [
                    ClinicalEvaluationItemSerializer(evaluations.item_payload(item)).data
                    for item in page
                ],
            }
        )


class EvaluationClaimView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Claim (or resume) an evaluation",
        request=None,
        responses={200: ClinicalEvaluationItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        user = _user(request)
        evaluation = evaluations.claim(
            evaluation_id=evaluation_id,
            user=user,
            profile=physician_profile(user),
            request=request,
        )
        body = _item(evaluation)
        response = Response(body)
        response["ETag"] = f'"{body["version"]}"'
        return response


class EvaluationReleaseView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Release a claimed evaluation back to the queue",
        parameters=[IF_MATCH],
        request=ReleaseReasonSerializer,
        responses={200: ClinicalEvaluationItemSerializer, **COMMON_ERRORS, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        version = evaluations.parse_if_match(request.headers.get("If-Match"))
        serializer = ReleaseReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        evaluation = evaluations.release(
            evaluation_id=evaluation_id,
            user=_user(request),
            version=version,
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        body = _item(evaluation)
        response = Response(body)
        response["ETag"] = f'"{body["version"]}"'
        return response


class EvaluationPacketView(APIView):
    permission_classes = [IsVerifiedPhysicianOrAdmin]

    @extend_schema(
        tags=["clinical"],
        summary="Review packet (assignee or admin; audited)",
        responses={200: ReviewPacketSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, evaluation_id: str) -> Response:
        body = evaluations.packet(evaluation_id=evaluation_id, user=_user(request), request=request)
        response = Response(ReviewPacketSerializer(body).data)
        response["ETag"] = f'"{body["version"]}"'
        return response


class LabPanelCatalogView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Lab panels a physician can request",
        responses={200: LabPanelCatalogSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return Response(LabPanelCatalogSerializer(evaluations.lab_panel_catalog(), many=True).data)


class _DecisionView(APIView):
    permission_classes = [IsVerifiedPhysician]
    input_serializer: type[drf.Serializer[Any]]
    endpoint = ""

    def decide(self, request: Request, evaluation_id: str, version: int, data: Any) -> Any:
        raise NotImplementedError

    def run(self, request: Request, evaluation_id: str) -> Response:
        version = evaluations.parse_if_match(request.headers.get("If-Match"))
        serializer = self.input_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        return _idempotent(
            request,
            self.endpoint,
            {"version": version, **serializer.validated_data},
            str(evaluation_id),
            lambda: self.decide(request, evaluation_id, version, data),
        )


class RequestLabsView(_DecisionView):
    input_serializer = RequestLabsSerializer
    endpoint = "clinical.request_labs"

    def decide(self, request: Request, evaluation_id: str, version: int, data: Any) -> Any:
        evaluation = evaluations.request_labs(
            evaluation_id=evaluation_id,
            user=_user(request),
            version=version,
            data=data,
            request=request,
        )
        return _item(evaluation)

    @extend_schema(
        tags=["clinical"],
        summary="Request lab results (IN_REVIEW -> PENDING_LABS)",
        parameters=[IF_MATCH, IDEMPOTENCY],
        request=RequestLabsSerializer,
        responses={200: ClinicalEvaluationItemSerializer, **COMMON_ERRORS, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        return self.run(request, evaluation_id)


class RejectView(_DecisionView):
    input_serializer = RejectSerializer
    endpoint = "clinical.reject"

    def decide(self, request: Request, evaluation_id: str, version: int, data: Any) -> Any:
        evaluation = evaluations.reject(
            evaluation_id=evaluation_id,
            user=_user(request),
            version=version,
            data=data,
            request=request,
        )
        return _item(evaluation)

    @extend_schema(
        tags=["clinical"],
        summary="Reject (IN_REVIEW -> NOT_SUITABLE)",
        parameters=[IF_MATCH, IDEMPOTENCY],
        request=RejectSerializer,
        responses={200: ClinicalEvaluationItemSerializer, **COMMON_ERRORS, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        return self.run(request, evaluation_id)


class ApproveView(_DecisionView):
    input_serializer = ApproveSerializer
    endpoint = "clinical.approve"

    def decide(self, request: Request, evaluation_id: str, version: int, data: Any) -> Any:
        evaluation, prescription, treatments = evaluations.approve(
            evaluation_id=evaluation_id,
            user=_user(request),
            version=version,
            data=data,
            request=request,
        )
        body = {
            "evaluation": _item(evaluation),
            "prescription": {
                "id": prescription.pk,
                "start_date": prescription.start_date,
                "valid_until": prescription.valid_until,
                "items": [
                    {
                        "treatment_id": treatment.pk,
                        "product_id": treatment.product_id,
                        "dose": {
                            "value": float(treatment.dose_value),
                            "unit": treatment.dose_unit,
                            "label": treatment.dose_label,
                        },
                        "frequency": treatment.frequency,
                        "refills_total": treatment.refills_total or 0,
                    }
                    for treatment in treatments
                ],
            },
        }
        return dict(ApproveResponseSerializer(body).data)

    @extend_schema(
        tags=["clinical"],
        summary="Approve and prescribe (IN_REVIEW -> APPROVED)",
        parameters=[IF_MATCH, IDEMPOTENCY],
        request=ApproveSerializer,
        responses={200: ApproveResponseSerializer, **COMMON_ERRORS, **CONCURRENCY_ERRORS},
    )
    def post(self, request: Request, evaluation_id: str) -> Response:
        response = self.run(request, evaluation_id)
        if isinstance(response.data, dict) and "evaluation" in response.data:
            response["ETag"] = f'"{response.data["evaluation"]["version"]}"'
        return response


class ProductListView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Prescribable products and guardrails",
        parameters=[
            OpenApiParameter("program_id", OpenApiTypes.UUID),
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("page", OpenApiTypes.INT),
        ],
        responses={200: ProductSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = Product.objects.filter(is_active=True).order_by("name")
        program_id = _uuid_or_400(request.query_params.get("program_id"), "program_id")
        if program_id:
            queryset = queryset.filter(programs__pk=program_id)
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(active_ingredient__icontains=search)
            )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset.distinct(), request, view=self) or []
        rows = [
            {
                "id": item.pk,
                "name": item.name,
                "active_ingredient": item.active_ingredient,
                "form": item.form,
                "route": item.route,
                "allowed_doses": item.allowed_doses,
                "default_frequency": item.default_frequency,
                "starting_doses": item.starting_doses,
                "max_refills": item.max_refills,
            }
            for item in page
        ]
        return paginator.get_paginated_response(ProductSerializer(rows, many=True).data)


def _document_payload(document: Any) -> dict[str, Any]:
    return dict(
        PrescriptionDocumentSerializer(
            {
                "id": document.pk,
                "status": document.status,
                "content_type": document.content_type,
                "size_bytes": document.size_bytes,
                "created_at": document.created_at,
            }
        ).data
    )


class PrescriptionDocumentCreateView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Start a signed prescription PDF upload",
        request=PrescriptionDocumentCreateSerializer,
        responses={201: UploadInitSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = PrescriptionDocumentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document, target = documents.start_upload(
            user=_user(request), data=dict(serializer.validated_data), request=request
        )
        return Response(
            UploadInitSerializer({"id": document.pk, "upload": target}).data,
            status=status.HTTP_201_CREATED,
        )


class PrescriptionDocumentConfirmView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Confirm a prescription PDF upload and queue the scan",
        request=None,
        responses={200: PrescriptionDocumentSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, document_id: str) -> Response:
        document = documents.confirm_upload(
            user=_user(request), document_id=document_id, request=request
        )
        return Response(_document_payload(document))


# ---------------------------------------------------------------------------
# Section 5
# ---------------------------------------------------------------------------


class PatientListView(APIView):
    permission_classes = [IsVerifiedPhysicianOrAdmin]

    @extend_schema(
        tags=["clinical"],
        summary="My Patients",
        parameters=[
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                many=True,
                enum=[value for value, _ in PATIENT_LIST_STATUSES],
            ),
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("program_id", OpenApiTypes.UUID),
            OpenApiParameter("ordering", OpenApiTypes.STR, enum=["name", "-last_activity_at"]),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: PatientListItemSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        user = _user(request)
        statuses = request.query_params.getlist("status")
        allowed = {value for value, _ in PATIENT_LIST_STATUSES}
        if any(item not in allowed for item in statuses):
            raise ValidationError({"status": ["Unknown status."]})
        ordering = request.query_params.get("ordering", "name")
        if ordering not in ("name", "-last_activity_at"):
            raise ValidationError({"ordering": ["Unknown ordering."]})
        rows = clinical_treatments.list_patients(
            user,
            statuses=statuses,
            search=(request.query_params.get("search") or "").strip(),
            program_id=_uuid_or_400(request.query_params.get("program_id"), "program_id"),
            ordering=ordering,
        )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(cast(Any, rows), request, view=self) or []
        return paginator.get_paginated_response(PatientListItemSerializer(page, many=True).data)


class PatientChartView(APIView):
    permission_classes = [IsVerifiedPhysicianOrAdmin]

    @extend_schema(
        tags=["clinical"],
        summary="Patient chart (audited)",
        responses={
            200: inline_serializer(
                "PatientChart",
                fields={
                    "patient": ChartPatientSerializer(),
                    "latest_biometrics": BiometricsSerializer(allow_null=True),
                    "evaluations": PreviousEvaluationSerializer(many=True),
                    "treatments": ClinicianTreatmentSerializer(many=True),
                    "consultations": ClinicianConsultationItemSerializer(many=True),
                    "labs": LabAttachmentSerializer(many=True),
                    "message_thread_id": drf.UUIDField(allow_null=True),
                    "notes_count": drf.IntegerField(),
                },
            ),
            **COMMON_ERRORS,
        },
    )
    def get(self, request: Request, patient_id: str) -> Response:
        from apps.assessments.models import Assessment
        from apps.patients.services import get_or_create_profile, latest_measurement
        from apps.treatments.models import Treatment

        user = _user(request)
        patient = clinical_treatments.get_care_patient(user, patient_id)
        record_audit(
            actor=user,
            action="patient_chart.read",
            object_type="User",
            object_id=patient.pk,
            request=request,
        )
        measurement = latest_measurement(patient)
        contact = user.role == Role.ADMIN or clinical_treatments.is_treating(user, patient)
        profile = get_or_create_profile(patient)
        unit = (
            physician_profile(user).preferred_weight_unit if user.role == Role.PHYSICIAN else "KG"
        )
        consultations = Consultation.objects.select_related("consultation_type", "patient").filter(
            patient=patient
        )
        if user.role == Role.PHYSICIAN:
            consultations = consultations.filter(physician=user)
        thread = MessageThread.objects.filter(patient=patient, participant=user).first()
        brief = evaluations.patient_brief(patient, measurement)
        body = {
            "patient": {
                **brief,
                "date_of_birth": measurement.date_of_birth if measurement else None,
                "email": patient.email if contact else None,
                "phone": (profile.phone or None) if contact else None,
            },
            "latest_biometrics": evaluations.biometrics(measurement, unit),
            "evaluations": [
                {
                    "id": item.pk,
                    "program_name": item.assessment.program.name,
                    "status": item.status,
                    "decided_at": item.decided_at,
                }
                for item in Evaluation.objects.filter(assessment__user=patient)
                .select_related("assessment__program")
                .order_by("-submitted_at")
            ],
            "treatments": [
                clinical_treatments.treatment_payload(item)
                for item in Treatment.objects.filter(patient=patient)
                .select_related("program", "product", "patient")
                .order_by("-started_at")
            ],
            "consultations": [
                clinical_consultations.consultation_item(item)
                for item in consultations.order_by("-start")[:10]
            ],
            "labs": evaluations.lab_attachments(
                list(Assessment.objects.filter(user=patient).values_list("pk", flat=True))
            ),
            "message_thread_id": thread.pk if thread else None,
            "notes_count": ClinicalNote.objects.filter(patient=patient).count(),
        }
        return Response(
            {
                **body,
                "patient": ChartPatientSerializer(body["patient"]).data,
                "latest_biometrics": (
                    BiometricsSerializer(body["latest_biometrics"]).data
                    if body["latest_biometrics"]
                    else None
                ),
                "evaluations": PreviousEvaluationSerializer(body["evaluations"], many=True).data,
                "treatments": ClinicianTreatmentSerializer(body["treatments"], many=True).data,
                "consultations": ClinicianConsultationItemSerializer(
                    body["consultations"], many=True
                ).data,
                "labs": LabAttachmentSerializer(body["labs"], many=True).data,
            }
        )


class PatientNotesView(APIView):
    permission_classes = [IsVerifiedPhysicianOrAdmin]

    @extend_schema(
        tags=["clinical"],
        summary="Clinical notes (newest first; audited)",
        parameters=[OpenApiParameter("page", OpenApiTypes.INT)],
        responses={200: NoteSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request, patient_id: str) -> Response:
        user = _user(request)
        patient = clinical_treatments.get_care_patient(user, patient_id)
        record_audit(
            actor=user,
            action="clinical_notes.read",
            object_type="User",
            object_id=patient.pk,
            request=request,
        )
        notes = ClinicalNote.objects.filter(patient=patient).select_related(
            "author__clinician_profile"
        )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(notes, request, view=self) or []
        rows = [clinical_treatments.note_payload(note) for note in page]
        return paginator.get_paginated_response(NoteSerializer(rows, many=True).data)

    @extend_schema(
        tags=["clinical"],
        summary="Add an append-only clinical note",
        request=NoteCreateSerializer,
        responses={201: NoteSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, patient_id: str) -> Response:
        user = _user(request)
        if user.role != Role.PHYSICIAN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        patient = clinical_treatments.get_care_patient(user, patient_id)
        serializer = NoteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = clinical_treatments.add_note(
            author=user,
            patient=patient,
            kind=serializer.validated_data["kind"],
            body=serializer.validated_data["body"],
            consultation_id=serializer.validated_data.get("consultation_id"),
        )
        record_audit(
            actor=user,
            action="clinical_note.created",
            object_type="ClinicalNote",
            object_id=note.pk,
            request=request,
        )
        return Response(
            NoteSerializer(clinical_treatments.note_payload(note)).data,
            status=status.HTTP_201_CREATED,
        )


def _treatment_response(treatment: Any) -> dict[str, Any]:
    treatment.refresh_from_db()
    return dict(ClinicianTreatmentSerializer(clinical_treatments.treatment_payload(treatment)).data)


class TreatmentAdjustView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Adjust dose or schedule (new immutable version)",
        parameters=[IDEMPOTENCY],
        request=AdjustSerializer,
        responses={200: ClinicianTreatmentSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, treatment_id: str) -> Response:
        serializer = AdjustSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data["dose"] = dict(data["dose"])

        def run() -> dict[str, Any]:
            treatment = clinical_treatments.adjust(
                physician=_user(request), treatment_id=treatment_id, data=data, request=request
            )
            return _treatment_response(treatment)

        return _idempotent(
            request, "clinical.treatment_adjust", serializer.validated_data, treatment_id, run
        )


class TreatmentStatusView(APIView):
    permission_classes = [IsVerifiedPhysician]
    action_name = ""

    @extend_schema(
        tags=["clinical"],
        summary="Pause, resume or discontinue a treatment",
        parameters=[IDEMPOTENCY],
        request=StatusChangeSerializer,
        responses={200: ClinicianTreatmentSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, treatment_id: str) -> Response:
        serializer = StatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def run() -> dict[str, Any]:
            treatment = clinical_treatments.change_status(
                physician=_user(request),
                treatment_id=treatment_id,
                action=self.action_name,
                data=dict(serializer.validated_data),
                request=request,
            )
            return _treatment_response(treatment)

        return _idempotent(
            request,
            f"clinical.treatment_{self.action_name}",
            serializer.validated_data,
            treatment_id,
            run,
        )


class RenewalListView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Renewal requests from my patients",
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, enum=list(RenewalRequestStatus.values)),
            OpenApiParameter("page", OpenApiTypes.INT),
        ],
        responses={200: RenewalItemSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        wanted = request.query_params.get("status", RenewalRequestStatus.REQUESTED)
        if wanted not in RenewalRequestStatus.values:
            raise ValidationError({"status": ["Unknown status."]})
        queryset = clinical_treatments.renewals_for(_user(request)).filter(status=wanted)
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        rows = [clinical_treatments.renewal_payload(item) for item in page]
        return paginator.get_paginated_response(RenewalItemSerializer(rows, many=True).data)


class RenewalApproveView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Approve a renewal (adds refills)",
        parameters=[IDEMPOTENCY],
        request=RenewalApproveSerializer,
        responses={200: RenewalItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, renewal_id: str) -> Response:
        serializer = RenewalApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def run() -> dict[str, Any]:
            renewal = clinical_treatments.approve_renewal(
                physician=_user(request),
                renewal_id=renewal_id,
                data=dict(serializer.validated_data),
                request=request,
            )
            renewal.treatment.refresh_from_db()
            return dict(RenewalItemSerializer(clinical_treatments.renewal_payload(renewal)).data)

        return _idempotent(
            request, "clinical.renewal_approve", serializer.validated_data, renewal_id, run
        )


class RenewalDenyView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Deny a renewal",
        parameters=[IDEMPOTENCY],
        request=RenewalDenySerializer,
        responses={200: RenewalItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, renewal_id: str) -> Response:
        serializer = RenewalDenySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def run() -> dict[str, Any]:
            renewal = clinical_treatments.deny_renewal(
                physician=_user(request),
                renewal_id=renewal_id,
                data=dict(serializer.validated_data),
                request=request,
            )
            return dict(RenewalItemSerializer(clinical_treatments.renewal_payload(renewal)).data)

        return _idempotent(
            request, "clinical.renewal_deny", serializer.validated_data, renewal_id, run
        )


# ---------------------------------------------------------------------------
# Section 6
# ---------------------------------------------------------------------------


def _consultations_scope(user: User) -> Any:
    if user.role == Role.ADMIN:
        return Consultation.objects.select_related("consultation_type", "patient")
    return clinical_consultations.own_consultations(user)


class ClinicianConsultationListView(APIView):
    permission_classes = [IsVerifiedPhysicianOrAdmin]

    @extend_schema(
        tags=["clinical"],
        operation_id="clinical_consultations_list",
        summary="My consultations",
        parameters=[
            OpenApiParameter("scope", OpenApiTypes.STR, enum=["TODAY", "UPCOMING", "PAST"]),
            OpenApiParameter("status", OpenApiTypes.STR),
            OpenApiParameter("from", OpenApiTypes.DATE),
            OpenApiParameter("to", OpenApiTypes.DATE),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: ConsultationListResponseSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        user = _user(request)
        queryset = _consultations_scope(user)
        params = request.query_params
        now = timezone.now()
        scope = params.get("scope")
        if scope == "TODAY":
            if user.role == Role.PHYSICIAN:
                ids = [item.pk for item in clinical_consultations.today_consultations(user)]
                queryset = queryset.filter(pk__in=ids)
            else:
                queryset = queryset.filter(start__date=timezone.localdate())
            queryset = queryset.order_by("start")
        elif scope == "UPCOMING":
            queryset = queryset.filter(
                status__in=("REQUESTED", "CONFIRMED", "IN_PROGRESS"), end__gte=now
            ).order_by("start")
        elif scope == "PAST":
            queryset = queryset.exclude(
                status__in=("REQUESTED", "CONFIRMED", "IN_PROGRESS"), end__gte=now
            ).order_by("-start")
        elif scope is not None:
            raise ValidationError({"scope": ["Must be TODAY, UPCOMING or PAST."]})
        else:
            queryset = queryset.order_by("-start")
        status_filter = params.get("status")
        if status_filter:
            if status_filter not in ConsultationStatus.values:
                raise ValidationError({"status": ["Unknown status."]})
            queryset = queryset.filter(status=status_filter)
        date_from = _parse_date(params.get("from"), "from")
        date_to = _parse_date(params.get("to"), "to")
        if date_from:
            queryset = queryset.filter(start__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(start__date__lte=date_to)
        meta = {
            "this_week_count": (
                clinical_consultations.this_week_count(user) if user.role == Role.PHYSICIAN else 0
            ),
            "requested_count": _consultations_scope(user).filter(status="REQUESTED").count(),
        }
        if user.role == Role.ADMIN:
            record_audit(
                actor=user,
                action="consultation.clinical_list",
                object_type="Consultation",
                object_id="*",
                request=request,
            )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        return Response(
            {
                "meta": meta,
                "count": paginator.page.paginator.count,  # type: ignore[union-attr]
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": ClinicianConsultationItemSerializer(
                    [clinical_consultations.consultation_item(item) for item in page], many=True
                ).data,
            }
        )


def _detail(consultation: Consultation) -> dict[str, Any]:
    consultation.refresh_from_db()
    return dict(
        ClinicianConsultationDetailSerializer(
            clinical_consultations.consultation_detail(consultation)
        ).data
    )


class ClinicianConsultationDetailView(APIView):
    permission_classes = [IsVerifiedPhysicianOrAdmin]

    @extend_schema(
        tags=["clinical"],
        summary="Consultation detail (clinician)",
        responses={200: ClinicianConsultationDetailSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, consultation_id: str) -> Response:
        user = _user(request)
        consultation = _consultations_scope(user).filter(pk=consultation_id).first()
        if consultation is None:
            raise NotFound()
        record_audit(
            actor=user,
            action="consultation.clinical_read",
            object_type="Consultation",
            object_id=consultation.pk,
            request=request,
        )
        return Response(_detail(consultation))

    @extend_schema(
        tags=["clinical"],
        summary="Edit the agenda",
        request=AgendaSerializer,
        responses={200: ClinicianConsultationDetailSerializer, **COMMON_ERRORS},
    )
    def patch(self, request: Request, consultation_id: str) -> Response:
        user = _user(request)
        if user.role != Role.PHYSICIAN:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        serializer = AgendaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultation = clinical_consultations.update_agenda(
            physician=user,
            consultation_id=consultation_id,
            agenda=serializer.validated_data.get("agenda", ""),
        )
        return Response(_detail(consultation))


class ConsultationAcceptView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Accept consultation (REQUESTED -> CONFIRMED)",
        parameters=[IDEMPOTENCY],
        request=None,
        responses={200: ClinicianConsultationItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        def run() -> dict[str, Any]:
            consultation = clinical_consultations.accept(
                physician=_user(request), consultation_id=consultation_id, request=request
            )
            return dict(
                ClinicianConsultationItemSerializer(
                    clinical_consultations.consultation_item(consultation)
                ).data
            )

        return _idempotent(request, "clinical.consultation_accept", {}, consultation_id, run)


class ConsultationDeclineView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Decline a consultation request",
        request=DeclineSerializer,
        responses={200: ClinicianConsultationItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        serializer = DeclineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultation = clinical_consultations.decline(
            physician=_user(request),
            consultation_id=consultation_id,
            reason=serializer.validated_data["reason"],
            patient_note=serializer.validated_data.get("patient_note", ""),
            request=request,
        )
        return Response(
            ClinicianConsultationItemSerializer(
                clinical_consultations.consultation_item(consultation)
            ).data
        )


class ConsultationPhysicianRescheduleView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Move a consultation to another of my free slots",
        request=PhysicianRescheduleSerializer,
        responses={200: ClinicianConsultationItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        serializer = PhysicianRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultation = clinical_consultations.reschedule(
            physician=_user(request),
            consultation_id=consultation_id,
            start=serializer.validated_data["start"],
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return Response(
            ClinicianConsultationItemSerializer(
                clinical_consultations.consultation_item(consultation)
            ).data
        )


class ConsultationPhysicianCancelView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Cancel a consultation",
        request=RequiredReasonSerializer,
        responses={200: ClinicianConsultationItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        serializer = RequiredReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultation = clinical_consultations.cancel(
            physician=_user(request),
            consultation_id=consultation_id,
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return Response(
            ClinicianConsultationItemSerializer(
                clinical_consultations.consultation_item(consultation)
            ).data
        )


class ConsultationPhysicianJoinView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Join as host (owner token)",
        request=None,
        responses={200: JoinResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        body = clinical_consultations.join(
            physician=_user(request), consultation_id=consultation_id, request=request
        )
        return Response(JoinResponseSerializer(body).data)


class ConsultationCompleteView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Complete the visit with notes",
        parameters=[IDEMPOTENCY],
        request=CompleteSerializer,
        responses={200: ClinicianConsultationDetailSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        serializer = CompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if data.get("follow_up"):
            data["follow_up"] = dict(data["follow_up"])

        def run() -> dict[str, Any]:
            consultation = clinical_consultations.complete(
                physician=_user(request),
                consultation_id=consultation_id,
                data=data,
                request=request,
            )
            return _detail(consultation)

        return _idempotent(
            request,
            "clinical.consultation_complete",
            serializer.validated_data,
            consultation_id,
            run,
        )


class ConsultationNoShowView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["clinical"],
        summary="Mark a patient no-show",
        request=NoShowSerializer,
        responses={200: ClinicianConsultationItemSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        serializer = NoShowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        consultation = clinical_consultations.mark_no_show(
            physician=_user(request),
            consultation_id=consultation_id,
            note=serializer.validated_data.get("note", ""),
            request=request,
        )
        return Response(
            ClinicianConsultationItemSerializer(
                clinical_consultations.consultation_item(consultation)
            ).data
        )


# ---------------------------------------------------------------------------
# Section 7
# ---------------------------------------------------------------------------


def _rule_out(rule: AvailabilityRule) -> dict[str, Any]:
    return dict(AvailabilityRuleSerializer(clinical_consultations.rule_data(rule)).data)


class AvailabilityRuleListView(APIView):
    permission_classes = [IsPhysician]

    @extend_schema(
        tags=["physicians"],
        summary="My weekly availability rules",
        responses={200: AvailabilityRuleSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        rules = AvailabilityRule.objects.filter(physician=_user(request)).prefetch_related(
            "consultation_types"
        )
        return Response([_rule_out(rule) for rule in rules])

    @extend_schema(
        tags=["physicians"],
        summary="Create an availability rule",
        request=AvailabilityRuleSerializer,
        responses={201: AvailabilityRuleSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = AvailabilityRuleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = clinical_consultations.save_rule(
            physician=_user(request), data=dict(serializer.validated_data)
        )
        return Response(_rule_out(rule), status=status.HTTP_201_CREATED)


class AvailabilityRuleDetailView(APIView):
    permission_classes = [IsPhysician]

    def _get(self, request: Request, rule_id: str) -> AvailabilityRule:
        rule = AvailabilityRule.objects.filter(pk=rule_id, physician=_user(request)).first()
        if rule is None:
            raise NotFound()
        return rule

    @extend_schema(
        tags=["physicians"],
        summary="Update an availability rule",
        request=AvailabilityRuleSerializer,
        responses={200: AvailabilityRuleSerializer, **COMMON_ERRORS},
    )
    def patch(self, request: Request, rule_id: str) -> Response:
        rule = self._get(request, rule_id)
        serializer = AvailabilityRuleSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        rule = clinical_consultations.save_rule(
            physician=_user(request), data=dict(serializer.validated_data), rule=rule
        )
        return Response(_rule_out(rule))

    @extend_schema(
        tags=["physicians"],
        summary="Delete an availability rule",
        responses={204: None, **COMMON_ERRORS},
    )
    def delete(self, request: Request, rule_id: str) -> Response:
        clinical_consultations.delete_rule(
            physician=_user(request), rule=self._get(request, rule_id)
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


def _block_out(block: AvailabilityBlock) -> dict[str, Any]:
    return dict(TimeBlockSerializer(clinical_consultations.block_data(block)).data)


class TimeBlockListView(APIView):
    permission_classes = [IsPhysician]

    @extend_schema(
        tags=["physicians"],
        summary="My one-off time blocks",
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE),
            OpenApiParameter("to", OpenApiTypes.DATE),
        ],
        responses={200: TimeBlockSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        today = timezone.localdate()
        date_from = _parse_date(request.query_params.get("from"), "from") or today
        date_to = _parse_date(request.query_params.get("to"), "to") or today + timedelta(days=30)
        if date_to < date_from or (date_to - date_from).days > 62:
            raise ValidationError({"to": ["Must be 0 to 62 days after from."]})
        blocks = AvailabilityBlock.objects.filter(
            physician=_user(request),
            start__date__lte=date_to,
            end__date__gte=date_from,
        ).order_by("start")
        return Response([_block_out(block) for block in blocks])

    @extend_schema(
        tags=["physicians"],
        summary="Create a time block",
        request=TimeBlockSerializer,
        responses={201: TimeBlockSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = TimeBlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        block = clinical_consultations.save_block(
            physician=_user(request), data=dict(serializer.validated_data)
        )
        return Response(_block_out(block), status=status.HTTP_201_CREATED)


class TimeBlockDetailView(APIView):
    permission_classes = [IsPhysician]

    def _get(self, request: Request, block_id: str) -> AvailabilityBlock:
        block = AvailabilityBlock.objects.filter(pk=block_id, physician=_user(request)).first()
        if block is None:
            raise NotFound()
        return block

    @extend_schema(
        tags=["physicians"],
        summary="Update a time block",
        request=TimeBlockSerializer,
        responses={200: TimeBlockSerializer, **COMMON_ERRORS},
    )
    def patch(self, request: Request, block_id: str) -> Response:
        block = self._get(request, block_id)
        serializer = TimeBlockSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        block = clinical_consultations.save_block(
            physician=_user(request), data=dict(serializer.validated_data), block=block
        )
        return Response(_block_out(block))

    @extend_schema(
        tags=["physicians"],
        summary="Delete a time block",
        responses={204: None, **COMMON_ERRORS},
    )
    def delete(self, request: Request, block_id: str) -> Response:
        clinical_consultations.delete_block(
            physician=_user(request), block=self._get(request, block_id)
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class CalendarView(APIView):
    permission_classes = [IsVerifiedPhysician]

    @extend_schema(
        tags=["physicians"],
        summary="Calendar read model",
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE, required=True),
            OpenApiParameter("to", OpenApiTypes.DATE, required=True),
        ],
        responses={200: CalendarSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        date_from = _parse_date(request.query_params.get("from"), "from")
        date_to = _parse_date(request.query_params.get("to"), "to")
        if date_from is None or date_to is None:
            raise ValidationError({"from": ["from and to are required."]})
        if date_to < date_from or (date_to - date_from).days > 14:
            raise ValidationError({"to": ["Must be 0 to 14 days after from."]})
        body = clinical_consultations.calendar(_user(request), date_from, date_to)
        return Response(CalendarSerializer(body).data)
