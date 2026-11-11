"""Part 3 sections 2 and 3: profile, addresses, ubigeo, care team, overview."""

from __future__ import annotations

from typing import Any, cast

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.serializers import EmptyBodySerializer
from apps.consultations.clinicians import care_member_summary
from apps.core.permissions import IsPatient
from apps.core.schema import COMMON_ERRORS
from apps.patients import services
from apps.patients.models import Address, Ubigeo, UbigeoLevel
from apps.patients.overview import build_overview
from apps.patients.overview_serializers import OverviewSerializer
from apps.patients.serializers import (
    AddressInputSerializer,
    AddressSerializer,
    CareTeamMemberSerializer,
    PatientProfilePatchSerializer,
    PatientProfileSerializer,
    UbigeoSerializer,
)


def _user(request: Request) -> User:
    return cast(User, request.user)


def _own_address(request: Request, address_id: Any) -> Address:
    address = (
        Address.objects.select_related("ubigeo__parent__parent")
        .filter(pk=address_id, user=_user(request))
        .first()
    )
    if address is None:
        raise NotFound()
    return address


class PatientMeView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["patients"],
        summary="My patient profile and preferences",
        responses={200: PatientProfileSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        profile = services.get_or_create_profile(_user(request))
        return Response(PatientProfileSerializer(profile).data)

    @extend_schema(
        tags=["patients"],
        summary="Update phone, preferences or full name",
        description=(
            "Partial update. `date_of_birth`, `sex` and `email` are read-only "
            "(`400 read_only_field`)."
        ),
        request=PatientProfilePatchSerializer,
        responses={200: PatientProfileSerializer, **COMMON_ERRORS},
    )
    def patch(self, request: Request) -> Response:
        if not isinstance(request.data, dict):
            raise ValidationError({"non_field_errors": ["Expected an object."]})
        profile = services.update_profile(_user(request), dict(request.data))
        profile.user.refresh_from_db()
        return Response(PatientProfileSerializer(profile).data)


class AddressListCreateView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["patients"],
        summary="List my delivery addresses",
        responses={200: AddressSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = Address.objects.select_related("ubigeo__parent__parent").filter(
            user=_user(request)
        )
        return Response(AddressSerializer(queryset, many=True).data)

    @extend_schema(
        tags=["patients"],
        summary="Add a delivery address",
        request=AddressInputSerializer,
        responses={201: AddressSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = AddressInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data["ubigeo"] = services.resolve_district(data["ubigeo"])
        address = services.create_address(_user(request), data)
        address = _own_address(request, address.pk)
        return Response(AddressSerializer(address).data, status=status.HTTP_201_CREATED)


class AddressDetailView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["patients"],
        summary="Address detail",
        responses={200: AddressSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, address_id: str) -> Response:
        return Response(AddressSerializer(_own_address(request, address_id)).data)

    @extend_schema(
        tags=["patients"],
        summary="Update an address",
        request=AddressInputSerializer,
        responses={200: AddressSerializer, **COMMON_ERRORS},
    )
    def patch(self, request: Request, address_id: str) -> Response:
        address = _own_address(request, address_id)
        serializer = AddressInputSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        if "ubigeo" in data:
            data["ubigeo"] = services.resolve_district(data["ubigeo"])
        services.update_address(_user(request), address, data)
        return Response(AddressSerializer(_own_address(request, address_id)).data)

    @extend_schema(
        tags=["patients"],
        summary="Delete an address",
        responses={204: None, **COMMON_ERRORS},
    )
    def delete(self, request: Request, address_id: str) -> Response:
        services.delete_address(_user(request), _own_address(request, address_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class AddressDefaultView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["patients"],
        summary="Make an address the default",
        request=EmptyBodySerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request, address_id: str) -> Response:
        services.make_default(_user(request), _own_address(request, address_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class UbigeoView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["patients"],
        summary="Peru departments, provinces and districts (INEI ubigeo)",
        parameters=[
            OpenApiParameter(
                "level", OpenApiTypes.STR, required=True, enum=list(UbigeoLevel.values)
            ),
            OpenApiParameter("parent", OpenApiTypes.STR),
        ],
        responses={200: UbigeoSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        level = request.query_params.get("level")
        if level not in UbigeoLevel.values:
            raise ValidationError({"level": [f"Must be one of {', '.join(UbigeoLevel.values)}."]})
        parent = request.query_params.get("parent")
        queryset = Ubigeo.objects.filter(level=level)
        if level == UbigeoLevel.DEPARTMENT:
            if parent:
                raise ValidationError({"parent": ["Not allowed for DEPARTMENT."]})
        else:
            if not parent:
                raise ValidationError({"parent": ["This query parameter is required."]})
            queryset = queryset.filter(parent_id=parent)
        rows = [
            {"code": item.code, "name": item.name, "is_deliverable": item.is_deliverable}
            for item in queryset.order_by("code")
        ]
        response = Response(UbigeoSerializer(rows, many=True).data)
        response["Cache-Control"] = "public, max-age=86400"
        return response


class CareTeamView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["patients"],
        summary="People I can message and book",
        responses={200: CareTeamMemberSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        members = [care_member_summary(user) for user in services.care_team_users(_user(request))]
        return Response(CareTeamMemberSerializer(members, many=True).data)


class OverviewView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["patients"],
        summary="Dashboard overview (one call for the whole screen)",
        responses={200: OverviewSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return Response(OverviewSerializer(build_overview(_user(request))).data)
