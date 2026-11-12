"""Patient support tickets and public ticket categories (Part 5, 6.2; amendment B7)."""

from __future__ import annotations

from typing import Any

from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.backoffice.serializers import (
    PatientTicketCreateSerializer,
    TicketCategoryPublicSerializer,
    TicketMessageCreateSerializer,
)
from apps.catalog.models import TicketCategory
from apps.core.permissions import IsPatient
from apps.staff.api import PortalSchema, doc, each, page, params, query_params, user_of, validated
from apps.support import tickets
from apps.support.models import SupportTicket, TicketStatus

TAG = "support"


def _detail(ticket: SupportTicket) -> dict[str, Any]:
    body = tickets.ticket_payload(ticket)
    messages = ticket.messages.filter(is_internal=False).select_related("author")
    body["messages"] = [
        tickets.message_payload(message) for message in messages.order_by("created_at")
    ]
    return body


class PatientTicketListView(APIView):
    schema = PortalSchema()
    permission_classes = [IsPatient]
    throttle_scope = "ticket_create"

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            return [ScopedRateThrottle()]
        return []

    @doc(TAG, "My support tickets", parameters=query_params(["status", "page", "page_size"]))
    def get(self, request: Request) -> Response:
        query = params(request, {"status": tuple(TicketStatus.values)})
        queryset = tickets.patient_tickets(user_of(request))
        if query.get("status"):
            queryset = queryset.filter(status=query["status"])
        return page(request, self, queryset.order_by("-created_at"), each(tickets.ticket_payload))

    @doc(
        TAG,
        "Open a support ticket",
        request=PatientTicketCreateSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request) -> Response:
        data = validated(PatientTicketCreateSerializer, request)
        ticket = tickets.create_patient_ticket(user=user_of(request), data=data)
        return Response(tickets.ticket_payload(ticket), status=status.HTTP_201_CREATED)


class PatientTicketDetailView(APIView):
    schema = PortalSchema()
    permission_classes = [IsPatient]

    @doc(TAG, "Ticket detail with messages")
    def get(self, request: Request, ticket_id: str) -> Response:
        return Response(_detail(tickets.patient_ticket(user_of(request), ticket_id)))


class PatientTicketMessageView(APIView):
    schema = PortalSchema()
    permission_classes = [IsPatient]

    @doc(
        TAG,
        "Add a message to my ticket",
        request=TicketMessageCreateSerializer,
        responses={201: OpenApiTypes.OBJECT},
    )
    def post(self, request: Request, ticket_id: str) -> Response:
        data = validated(TicketMessageCreateSerializer, request)
        user = user_of(request)
        ticket = tickets.patient_ticket(user, ticket_id)
        message = tickets.patient_message(ticket=ticket, user=user, body=data["body"])
        return Response(tickets.message_payload(message), status=status.HTTP_201_CREATED)


class PatientTicketCloseView(APIView):
    schema = PortalSchema()
    permission_classes = [IsPatient]

    @doc(TAG, "Close my ticket")
    def post(self, request: Request, ticket_id: str) -> Response:
        user = user_of(request)
        ticket = tickets.patient_close(ticket=tickets.patient_ticket(user, ticket_id), user=user)
        return Response(tickets.ticket_payload(ticket))


class TicketCategoryListView(APIView):
    schema = PortalSchema()
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    @doc(
        "public",
        "Support ticket categories",
        auth=[],
        responses={200: TicketCategoryPublicSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        rows = TicketCategory.objects.filter(is_active=True).order_by("position", "code")
        return Response(TicketCategoryPublicSerializer(rows, many=True).data)
