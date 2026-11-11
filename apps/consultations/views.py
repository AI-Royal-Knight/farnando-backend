"""Part 3 section 4: consultation types, physicians, availability, booking, video."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any, cast

from django.db.models import Q, QuerySet
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.permissions import IsPatientOrAdmin
from apps.assessments.throttles import MethodScopedRateThrottle
from apps.consultations import services
from apps.consultations.availability import compute_slots, next_available_at, physician_timezone
from apps.consultations.clinicians import physician_summary
from apps.consultations.models import (
    CareRole,
    ClinicianProfile,
    Consultation,
    ConsultationStatus,
    ConsultationType,
)
from apps.consultations.serializers import (
    AvailabilitySerializer,
    ConsultationCreateSerializer,
    ConsultationSerializer,
    ConsultationTypeSerializer,
    JoinResponseSerializer,
    PhysicianListItemSerializer,
    ReasonSerializer,
    RescheduleSerializer,
)
from apps.core.audit import record_audit
from apps.core.idempotency import execute
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.permissions import IsPatient
from apps.core.schema import COMMON_ERRORS

UPCOMING_STATUSES = (
    ConsultationStatus.REQUESTED,
    ConsultationStatus.CONFIRMED,
    ConsultationStatus.IN_PROGRESS,
)


def _user(request: Request) -> User:
    return cast(User, request.user)


def _consultations_for(user: Any) -> QuerySet[Consultation]:
    queryset = Consultation.objects.select_related(
        "consultation_type", "physician__clinician_profile", "patient"
    )
    if user.role == Role.PATIENT:
        return queryset.filter(patient=user)
    if user.role == Role.ADMIN:
        return queryset
    return queryset.none()


def _get_consultation(request: Request, consultation_id: Any, *, own: bool) -> Consultation:
    queryset = _consultations_for(_user(request))
    if own:
        queryset = queryset.filter(patient=_user(request))
    consultation = queryset.filter(pk=consultation_id).first()
    if consultation is None:
        raise NotFound()
    if consultation.patient_id != request.user.pk:
        record_audit(
            actor=request.user,
            action="consultation.read",
            object_type="Consultation",
            object_id=consultation.pk,
            request=request,
        )
    return consultation


def _parse_date(value: str | None, field: str) -> date:
    if not value:
        raise ValidationError({field: ["This query parameter is required."]})
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError({field: ["Use YYYY-MM-DD."]}) from exc


class ConsultationTypeListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["consultations"],
        summary="Consultation types",
        responses={200: ConsultationTypeSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = ConsultationType.objects.filter(is_active=True)
        return Response(ConsultationTypeSerializer(queryset, many=True).data)


class PhysicianListView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["consultations"],
        summary="Physicians accepting consultations (Select Physician modal)",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("specialty", OpenApiTypes.STR),
            OpenApiParameter("available_within_days", OpenApiTypes.INT),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: PhysicianListItemSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        profiles = ClinicianProfile.objects.select_related("user").filter(
            care_role=CareRole.PHYSICIAN,
            accepts_new_consultations=True,
            verification_status="VERIFIED",
            user__is_active=True,
            user__role=Role.PHYSICIAN,
        )
        search = (request.query_params.get("search") or "").strip()
        if search:
            profiles = profiles.filter(
                Q(display_name__icontains=search)
                | Q(user__full_name__icontains=search)
                | Q(specialty__icontains=search)
                | Q(license_label__icontains=search)
            )
        specialty = (request.query_params.get("specialty") or "").strip()
        if specialty:
            profiles = profiles.filter(specialty__iexact=specialty)
        within_raw = request.query_params.get("available_within_days")
        within: int | None = None
        if within_raw is not None:
            try:
                within = int(within_raw)
            except ValueError as exc:
                raise ValidationError({"available_within_days": ["Must be an integer."]}) from exc
            if not 1 <= within <= 30:
                raise ValidationError({"available_within_days": ["Must be between 1 and 30."]})
        initial_type = (
            ConsultationType.objects.filter(is_active=True).order_by("-is_initial", "position")
        ).first()
        rows: list[dict[str, Any]] = []
        for profile in profiles.order_by("display_name"):
            next_at = (
                next_available_at(profile.user, initial_type, within_days=within or 30)
                if initial_type is not None
                else None
            )
            if within is not None and next_at is None:
                continue
            row = physician_summary(profile.user)
            row.update(
                {
                    "specialty": profile.specialty,
                    "license_label": profile.license_label or None,
                    "next_available_at": next_at,
                }
            )
            rows.append(row)
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(cast(Any, rows), request, view=self)
        return paginator.get_paginated_response(PhysicianListItemSerializer(page, many=True).data)


class PhysicianAvailabilityView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["consultations"],
        summary="Open slots for a physician (computed live)",
        parameters=[
            OpenApiParameter("from", OpenApiTypes.DATE, required=True),
            OpenApiParameter("to", OpenApiTypes.DATE, required=True),
            OpenApiParameter("consultation_type_id", OpenApiTypes.UUID, required=True),
        ],
        responses={200: AvailabilitySerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, physician_id: str) -> Response:
        physician = User.objects.filter(
            pk=physician_id,
            role=Role.PHYSICIAN,
            is_active=True,
            clinician_profile__care_role=CareRole.PHYSICIAN,
            clinician_profile__accepts_new_consultations=True,
            clinician_profile__verification_status="VERIFIED",
        ).first()
        if physician is None:
            raise NotFound()
        date_from = _parse_date(request.query_params.get("from"), "from")
        date_to = _parse_date(request.query_params.get("to"), "to")
        if date_to < date_from:
            raise ValidationError({"to": ["Must be on or after from."]})
        if date_to > date_from + timedelta(days=31):
            raise ValidationError({"to": ["Must be at most 31 days after from."]})
        type_id = request.query_params.get("consultation_type_id")
        if not type_id:
            raise ValidationError({"consultation_type_id": ["This query parameter is required."]})
        try:
            type_uuid = uuid.UUID(type_id)
        except ValueError as exc:
            raise ValidationError({"consultation_type_id": ["Must be a valid UUID."]}) from exc
        consultation_type = ConsultationType.objects.filter(pk=type_uuid, is_active=True).first()
        if consultation_type is None:
            raise ValidationError({"consultation_type_id": ["Unknown consultation type."]})
        slots = compute_slots(physician, consultation_type, date_from, date_to)
        body = {
            "physician_id": physician.pk,
            "timezone": physician_timezone(physician),
            "slot_minutes": consultation_type.duration_minutes,
            "slots": [{"start": slot.start, "end": slot.end} for slot in slots],
        }
        response = Response(AvailabilitySerializer(body).data)
        response["Cache-Control"] = "no-store"
        return response


class ConsultationListCreateView(APIView):
    permission_classes = [IsPatientOrAdmin]
    throttle_classes = [MethodScopedRateThrottle]
    throttle_scopes = {"POST": "consultation_create"}

    @extend_schema(
        tags=["consultations"],
        summary="My consultations",
        parameters=[
            OpenApiParameter("scope", OpenApiTypes.STR, enum=["UPCOMING", "PAST"]),
            OpenApiParameter("status", OpenApiTypes.STR, enum=list(ConsultationStatus.values)),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: ConsultationSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = _consultations_for(_user(request))
        now = timezone.now()
        scope = request.query_params.get("scope")
        if scope is not None and scope not in ("UPCOMING", "PAST"):
            raise ValidationError({"scope": ["Must be UPCOMING or PAST."]})
        status_filter = request.query_params.get("status")
        if status_filter is not None:
            if status_filter not in ConsultationStatus.values:
                raise ValidationError({"status": ["Unknown consultation status."]})
            queryset = queryset.filter(status=status_filter)
        if scope == "UPCOMING":
            queryset = queryset.filter(status__in=UPCOMING_STATUSES, end__gte=now).order_by("start")
        elif scope == "PAST":
            queryset = queryset.exclude(status__in=UPCOMING_STATUSES, end__gte=now).order_by(
                "-start"
            )
        else:
            queryset = queryset.order_by("-start")
        if _user(request).role == Role.ADMIN:
            record_audit(
                actor=request.user,
                action="consultation.list",
                object_type="Consultation",
                object_id="*",
                request=request,
            )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ConsultationSerializer(page, many=True).data)

    @extend_schema(
        tags=["consultations"],
        summary="Request a consultation (holds the slot)",
        description="Requires Idempotency-Key. Throttled to 10/hour.",
        request=ConsultationCreateSerializer,
        responses={201: ConsultationSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        if _user(request).role != Role.PATIENT:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied()
        serializer = ConsultationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = cast(User, request.user)

        def create() -> tuple[int, dict[str, Any]]:
            consultation = services.book(
                patient=user, data=dict(serializer.validated_data), request=request
            )
            return status.HTTP_201_CREATED, dict(ConsultationSerializer(consultation).data)

        result = execute(
            key=request.headers.get("Idempotency-Key"),
            endpoint="consultations.create",
            payload=serializer.validated_data,
            scope=str(user.pk),
            callback=create,
        )
        return Response(result.body, status=result.status_code)


class ConsultationDetailView(APIView):
    permission_classes = [IsPatientOrAdmin]

    @extend_schema(
        tags=["consultations"],
        summary="Consultation detail",
        responses={200: ConsultationSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, consultation_id: str) -> Response:
        consultation = _get_consultation(request, consultation_id, own=False)
        return Response(ConsultationSerializer(consultation).data)


class ConsultationRescheduleView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["consultations"],
        summary="Move to another open slot (physician must accept again)",
        description="Requires Idempotency-Key.",
        request=RescheduleSerializer,
        responses={200: ConsultationSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        consultation = _get_consultation(request, consultation_id, own=True)
        serializer = RescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def run() -> tuple[int, dict[str, Any]]:
            updated = services.reschedule(
                consultation=consultation,
                start=serializer.validated_data["start"],
                request=request,
            )
            return status.HTTP_200_OK, dict(ConsultationSerializer(updated).data)

        result = execute(
            key=request.headers.get("Idempotency-Key"),
            endpoint="consultations.reschedule",
            payload=serializer.validated_data,
            scope=f"{request.user.pk}:{consultation.pk}",
            callback=run,
        )
        return Response(result.body, status=result.status_code)


class ConsultationCancelView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["consultations"],
        summary="Cancel a consultation",
        request=ReasonSerializer,
        responses={200: ConsultationSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        consultation = _get_consultation(request, consultation_id, own=True)
        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = services.cancel(
            consultation=consultation,
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        return Response(ConsultationSerializer(updated).data)


class ConsultationJoinView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["consultations"],
        summary="Join Video Call",
        request=None,
        responses={200: JoinResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        consultation = _get_consultation(request, consultation_id, own=True)
        body = services.join(
            consultation=consultation, user=cast(User, request.user), request=request
        )
        return Response(JoinResponseSerializer(body).data)


class ConsultationLeaveView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["consultations"],
        summary="End Call (patient side)",
        request=None,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request, consultation_id: str) -> Response:
        consultation = _get_consultation(request, consultation_id, own=True)
        services.leave(consultation=consultation, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
