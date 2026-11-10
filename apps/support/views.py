"""Anonymous contact and complaints-book endpoints."""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.captcha import verify_captcha
from apps.core.idempotency import execute
from apps.core.schema import COMMON_ERRORS
from apps.support.models import ComplaintBookEntry
from apps.support.serializers import (
    ComplaintCreatedSerializer,
    ComplaintCreateSerializer,
    ComplaintStatusSerializer,
    ContactMessageResponseSerializer,
    ContactMessageSerializer,
)
from apps.support.services import ComplaintService, ContactMessageService, client_ip
from apps.support.throttles import ContactEmailThrottle


class ContactMessageCreateView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "contact"
    throttle_classes = [ScopedRateThrottle, ContactEmailThrottle]

    @extend_schema(
        tags=["public"],
        summary="Send a public contact message",
        auth=[],
        request=ContactMessageSerializer,
        responses={201: ContactMessageResponseSerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample(
                "Contact message",
                value={
                    "full_name": "Maria Gonzalez",
                    "email": "maria@example.com",
                    "message": "I would like more information about the programme.",
                    "captcha_token": "test",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = ContactMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verify_captcha(
            token=serializer.validated_data.get("captcha_token"),
            remote_ip=client_ip(request._request),
        )
        message = ContactMessageService.create(
            data=serializer.validated_data, request=request._request
        )
        return Response(
            ContactMessageResponseSerializer(message).data,
            status=status.HTTP_201_CREATED,
        )


class ComplaintCreateView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "complaint"

    @extend_schema(
        tags=["public"],
        summary="File a complaints-book entry",
        description=(
            "Requires Idempotency-Key: replayed submissions return the same complaint number."
        ),
        auth=[],
        request=ComplaintCreateSerializer,
        responses={201: ComplaintCreatedSerializer, **COMMON_ERRORS},
        examples=[
            OpenApiExample(
                "Complaint",
                value={
                    "full_name": "Maria Gonzalez",
                    "email": "maria@example.com",
                    "category_code": "SERVICE",
                    "filing_type": "RECLAMO",
                    "description": "The consultation did not start at the scheduled time.",
                    "captcha_token": "test",
                },
                request_only=True,
            )
        ],
    )
    def post(self, request: Request) -> Response:
        serializer = ComplaintCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def create() -> tuple[int, dict[str, Any]]:
            verify_captcha(
                token=serializer.validated_data.get("captcha_token"),
                remote_ip=client_ip(request._request),
            )
            entry = ComplaintService.create(
                data=serializer.validated_data, request=request._request
            )
            return status.HTTP_201_CREATED, dict(ComplaintCreatedSerializer(entry).data)

        result = execute(
            key=request.headers.get("Idempotency-Key"),
            endpoint="public.complaints.create",
            payload=serializer.validated_data,
            scope=client_ip(request._request) or "anonymous",
            callback=create,
        )
        return Response(result.body, status=result.status_code)


class ComplaintStatusView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "complaint_status"

    @extend_schema(
        tags=["public"],
        summary="Check a complaints-book entry",
        description="Requires both the complaint number and the filing email; any mismatch is 404.",
        auth=[],
        responses={200: ComplaintStatusSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, complaint_number: str) -> Response:
        email = str(request.query_params.get("email", "")).strip().lower()
        if not email:
            raise NotFound()
        complaint = ComplaintBookEntry.objects.filter(
            complaint_number=complaint_number, email=email
        ).first()
        if complaint is None:
            raise NotFound()
        return Response(ComplaintStatusSerializer(complaint).data)
