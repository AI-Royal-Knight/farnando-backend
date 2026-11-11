"""Part 3 section 5: treatments (read-only for patients)."""

from __future__ import annotations

from typing import Any, cast

from django.db.models import QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.permissions import IsPatientOrAdmin
from apps.assessments.serializers import DownloadUrlSerializer
from apps.core.audit import record_audit
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.permissions import IsPatient
from apps.core.schema import COMMON_ERRORS
from apps.treatments import services
from apps.treatments.models import Treatment, TreatmentStatus
from apps.treatments.serializers import (
    RenewalRequestInputSerializer,
    RenewalRequestSerializer,
    TreatmentDetailSerializer,
    TreatmentListItemSerializer,
)


def _user(request: Request) -> User:
    return cast(User, request.user)


def _treatments_for(user: Any) -> QuerySet[Treatment]:
    queryset = Treatment.objects.select_related(
        "program",
        "product",
        "patient",
        "evaluation__prescription",
        "prescribing_physician__clinician_profile",
    )
    if user.role == Role.PATIENT:
        return queryset.filter(patient=user)
    if user.role == Role.ADMIN:
        return queryset
    return queryset.none()


def _get_treatment(request: Request, treatment_id: Any, *, own: bool = False) -> Treatment:
    queryset = _treatments_for(_user(request))
    if own:
        queryset = queryset.filter(patient=_user(request))
    treatment = queryset.filter(pk=treatment_id).first()
    if treatment is None:
        raise NotFound()
    if treatment.patient_id != request.user.pk:
        record_audit(
            actor=request.user,
            action="treatment.read",
            object_type="Treatment",
            object_id=treatment.pk,
            request=request,
        )
    return treatment


def _context(request: Request) -> dict[str, Any]:
    if _user(request).role == Role.PATIENT:
        return {"tz_name": services.patient_timezone(_user(request))}
    return {}


class TreatmentListView(APIView):
    permission_classes = [IsPatientOrAdmin]

    @extend_schema(
        tags=["treatments"],
        summary="My treatments",
        parameters=[
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                description="Comma-separated TreatmentStatus values. Default ACTIVE,PAUSED.",
            ),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: TreatmentListItemSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        raw = request.query_params.get("status")
        statuses = (
            [value.strip() for value in raw.split(",") if value.strip()]
            if raw
            else [TreatmentStatus.ACTIVE, TreatmentStatus.PAUSED]
        )
        unknown = [value for value in statuses if value not in TreatmentStatus.values]
        if unknown or not statuses:
            raise ValidationError({"status": ["Unknown treatment status."]})
        queryset = _treatments_for(_user(request)).filter(status__in=statuses)
        if _user(request).role == Role.ADMIN:
            record_audit(
                actor=request.user,
                action="treatment.list",
                object_type="Treatment",
                object_id="*",
                request=request,
            )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = TreatmentListItemSerializer(page, many=True, context=_context(request)).data
        return paginator.get_paginated_response(data)


class TreatmentDetailView(APIView):
    permission_classes = [IsPatientOrAdmin]

    @extend_schema(
        tags=["treatments"],
        summary="Treatment detail",
        responses={200: TreatmentDetailSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, treatment_id: str) -> Response:
        treatment = _get_treatment(request, treatment_id)
        return Response(TreatmentDetailSerializer(treatment, context=_context(request)).data)


class TreatmentPrescriptionDownloadView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["treatments"],
        summary="Pre-signed prescription PDF (5 minutes)",
        responses={200: DownloadUrlSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, treatment_id: str) -> Response:
        treatment = _get_treatment(request, treatment_id, own=True)
        body = services.download_prescription(treatment=treatment, request=request)
        return Response(DownloadUrlSerializer(body).data)


class TreatmentRenewalRequestView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["treatments"],
        summary="Ask the physician to renew a prescription",
        request=RenewalRequestInputSerializer,
        responses={201: RenewalRequestSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, treatment_id: str) -> Response:
        treatment = _get_treatment(request, treatment_id, own=True)
        serializer = RenewalRequestInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        renewal = services.request_renewal(
            treatment=treatment,
            note=serializer.validated_data.get("note", ""),
            request=request,
        )
        body = {"id": renewal.pk, "status": renewal.status, "created_at": renewal.created_at}
        return Response(RenewalRequestSerializer(body).data, status=status.HTTP_201_CREATED)
