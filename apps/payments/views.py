"""Part 3 sections 6.7 and 7: saved cards, payment polling, provider webhooks."""

from __future__ import annotations

from typing import Any, cast

import structlog
from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.accounts.serializers import EmptyBodySerializer
from apps.assessments.models import Payment
from apps.assessments.permissions import IsPatientOrAdmin
from apps.assessments.serializers import PaymentSerializer
from apps.core.audit import record_audit
from apps.core.exceptions import ApplicationError, PayloadTooLarge
from apps.core.permissions import IsPatient
from apps.core.schema import COMMON_ERRORS
from apps.payments import culqi, daily, services
from apps.payments.models import SavedPaymentMethod, WebhookProvider
from apps.payments.serializers import (
    PaymentMethodCreateSerializer,
    PaymentMethodSerializer,
    WebhookAckSerializer,
)

logger = structlog.get_logger(__name__)


class WebhookUnauthorized(ApplicationError):
    """401 without a WWW-Authenticate scheme (DRF would otherwise turn it into 403)."""

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = "Invalid webhook signature."
    default_code = "not_authenticated"


def _user(request: Request) -> User:
    return cast(User, request.user)


def _own_method(request: Request, method_id: Any) -> SavedPaymentMethod:
    method = services.active_methods(_user(request)).filter(pk=method_id).first()
    if method is None:
        raise NotFound()
    return method


class PaymentDetailView(APIView):
    permission_classes = [IsPatientOrAdmin]

    @extend_schema(
        tags=["payments"],
        summary="Payment status (poll after checkout)",
        responses={200: PaymentSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, payment_id: str) -> Response:
        queryset = Payment.objects.filter(pk=payment_id)
        if _user(request).role != Role.ADMIN:
            queryset = queryset.filter(user=_user(request))
        payment = queryset.first()
        if payment is None:
            raise NotFound()
        if payment.user_id != request.user.pk:
            record_audit(
                actor=request.user,
                action="payment.read",
                object_type="Payment",
                object_id=payment.pk,
                request=request,
            )
        return Response(PaymentSerializer(payment).data)


class PaymentMethodListCreateView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["payments"],
        summary="My saved cards",
        responses={200: PaymentMethodSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        methods = services.active_methods(_user(request)).order_by("-is_default", "-created_at")
        return Response(PaymentMethodSerializer(methods, many=True).data)

    @extend_schema(
        tags=["payments"],
        summary="Save a card from a provider token",
        request=PaymentMethodCreateSerializer,
        responses={201: PaymentMethodSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = PaymentMethodCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        method = services.save_card(
            user=_user(request), provider_token=serializer.validated_data["provider_token"]
        )
        method.refresh_from_db()
        return Response(PaymentMethodSerializer(method).data, status=status.HTTP_201_CREATED)


class PaymentMethodDetailView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["payments"],
        summary="Delete a saved card",
        responses={204: None, **COMMON_ERRORS},
    )
    def delete(self, request: Request, method_id: str) -> Response:
        services.delete_method(_user(request), _own_method(request, method_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class PaymentMethodDefaultView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["payments"],
        summary="Make a card the default",
        request=EmptyBodySerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request, method_id: str) -> Response:
        services.set_default_method(_user(request), _own_method(request, method_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


class _WebhookView(APIView):
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes: list[Any] = []
    provider: str = ""

    def verify(self, raw: bytes, request: Request) -> bool:
        raise NotImplementedError

    def identify(self, payload: dict[str, Any]) -> tuple[str, str]:
        raise NotImplementedError

    def handle(self, request: Request) -> Response:
        limit = int(settings.WEBHOOK_MAX_BODY_BYTES)
        declared = request.META.get("CONTENT_LENGTH") or "0"
        if declared.isdigit() and int(declared) > limit:
            raise PayloadTooLarge()
        raw = request.body
        if len(raw) > limit:
            raise PayloadTooLarge()
        if not self.verify(raw, request):
            logger.warning("webhook_signature_invalid", provider=self.provider)
            raise WebhookUnauthorized()
        try:
            payload = services.parse_body(raw)
            event_id, event_type = self.identify(payload)
        except (ValueError, culqi.InvalidPayload, daily.InvalidPayload) as exc:
            raise ValidationError({"body": ["Malformed webhook payload."]}) from exc
        event, created = services.store_event(
            provider=self.provider,
            event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
        if created:
            services.process_event(event)
        return Response({"received": True})


class CulqiWebhookView(_WebhookView):
    provider = WebhookProvider.CULQI

    def verify(self, raw: bytes, request: Request) -> bool:
        return culqi.verify_signature(raw, request.headers)

    def identify(self, payload: dict[str, Any]) -> tuple[str, str]:
        parsed = culqi.parse(payload)
        return parsed.event_id, parsed.event_type

    @extend_schema(
        tags=["webhooks"],
        summary="Culqi payment events (signature verified)",
        request=None,
        responses={200: WebhookAckSerializer, **COMMON_ERRORS},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        return self.handle(request)


class DailyWebhookView(_WebhookView):
    provider = WebhookProvider.DAILY

    def verify(self, raw: bytes, request: Request) -> bool:
        return daily.verify_signature(raw, request.headers)

    def identify(self, payload: dict[str, Any]) -> tuple[str, str]:
        parsed = daily.parse(payload)
        return parsed.event_id, parsed.event_type

    @extend_schema(
        tags=["webhooks"],
        summary="Daily video events (signature verified)",
        request=None,
        responses={200: WebhookAckSerializer, **COMMON_ERRORS},
        auth=[],
    )
    def post(self, request: Request) -> Response:
        return self.handle(request)
