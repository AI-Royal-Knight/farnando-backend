"""Part 3 section 6: packages, quote, checkout, orders and subscriptions."""

from __future__ import annotations

from typing import Any, cast

from django.conf import settings
from django.db.models import Count, Q, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.accounts.serializers import EmptyBodySerializer
from apps.assessments.models import Program
from apps.assessments.permissions import IsPatientOrAdmin
from apps.assessments.serializers import PaymentSerializer
from apps.assessments.services import money
from apps.assessments.throttles import MethodScopedRateThrottle
from apps.core.audit import record_audit
from apps.core.exceptions import EvaluationRequired
from apps.core.idempotency import execute
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.permissions import IsPatient
from apps.core.schema import COMMON_ERRORS, error_response
from apps.orders import services
from apps.orders.models import (
    INACTIVE_ORDER_STATUSES,
    Order,
    OrderStatus,
    OrderStatusGroup,
    Package,
    Subscription,
)
from apps.orders.serializers import (
    OrderCreateSerializer,
    OrderListItemSerializer,
    OrderSerializer,
    OrderSummaryCountsSerializer,
    PackageSerializer,
    PauseInputSerializer,
    PayOrderSerializer,
    QuoteInputSerializer,
    QuoteSerializer,
    ReasonInputSerializer,
    SubscriptionCancelInputSerializer,
    SubscriptionSerializer,
)


def _user(request: Request) -> User:
    return cast(User, request.user)


def _orders_for(user: Any) -> QuerySet[Order]:
    queryset = Order.objects.prefetch_related("items", "status_history", "payments")
    if user.role == Role.PATIENT:
        return queryset.filter(user=user)
    if user.role == Role.ADMIN:
        return queryset
    return queryset.none()


def _get_order(request: Request, order_id: Any, *, own: bool = False) -> Order:
    queryset = _orders_for(_user(request))
    if own:
        queryset = queryset.filter(user=_user(request))
    order = queryset.filter(pk=order_id).first()
    if order is None:
        raise NotFound()
    if order.user_id != request.user.pk:
        record_audit(
            actor=request.user,
            action="order.read",
            object_type="Order",
            object_id=order.pk,
            request=request,
        )
    return order


def _own_subscription(request: Request, subscription_id: Any) -> Subscription:
    subscription = (
        Subscription.objects.select_related("package", "payment_method")
        .prefetch_related("package__items")
        .filter(pk=subscription_id, user=_user(request))
        .first()
    )
    if subscription is None:
        raise NotFound()
    return subscription


class ProgramPackagesView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["orders"],
        summary="Packages I can buy for an approved program",
        responses={200: PackageSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request, program_id: str) -> Response:
        if not Program.objects.filter(pk=program_id).exists():
            raise NotFound()
        if not services.has_approved_evaluation(_user(request), program_id):
            raise EvaluationRequired()
        packages = Package.objects.filter(program_id=program_id, is_active=True).prefetch_related(
            "items__product"
        )
        return Response(PackageSerializer(packages, many=True).data)


class OrderQuoteView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["orders"],
        summary="Checkout totals (nothing is created)",
        request=QuoteInputSerializer,
        responses={200: QuoteSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = QuoteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        checkout = services.build_checkout(_user(request), dict(serializer.validated_data))
        pricing = checkout.pricing
        body = {
            "items": [
                {
                    "product_id": item["product_id"],
                    "name": item["name"],
                    "description": item["description"],
                    "quantity": item["quantity"],
                    "unit_price": money(item["unit_price"], pricing.currency),
                    "image_url": item["image_url"],
                }
                for item in checkout.items
            ],
            "subtotal": money(pricing.subtotal, pricing.currency),
            "shipping": money(pricing.shipping, pricing.currency),
            "tax": money(pricing.tax, pricing.currency),
            "total": money(pricing.total, pricing.currency),
            "estimated_delivery_days": {
                "min": settings.ORDER_DELIVERY_DAYS_MIN,
                "max": settings.ORDER_DELIVERY_DAYS_MAX,
            },
        }
        return Response(QuoteSerializer(body).data)


class OrderListCreateView(APIView):
    permission_classes = [IsPatientOrAdmin]
    throttle_classes = [MethodScopedRateThrottle]
    throttle_scopes = {"POST": "order_create"}

    @extend_schema(
        tags=["orders"],
        operation_id="orders_list",
        summary="My orders",
        parameters=[
            OpenApiParameter("status_group", OpenApiTypes.STR, enum=list(OrderStatusGroup.values)),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={
            200: inline_serializer(
                "OrderListResponse",
                fields={
                    "summary": OrderSummaryCountsSerializer(),
                    "count": drf_serializers.IntegerField(),
                    "next": drf_serializers.CharField(allow_null=True),
                    "previous": drf_serializers.CharField(allow_null=True),
                    "results": OrderListItemSerializer(many=True),
                },
            ),
            **COMMON_ERRORS,
        },
    )
    def get(self, request: Request) -> Response:
        group = request.query_params.get("status_group", OrderStatusGroup.ALL)
        if group not in OrderStatusGroup.values:
            raise ValidationError({"status_group": ["Must be ALL, ACTIVE or DELIVERED."]})
        base = _orders_for(_user(request))
        counts = base.aggregate(
            total_count=Count("id"),
            active_count=Count("id", filter=~Q(status__in=INACTIVE_ORDER_STATUSES)),
            delivered_count=Count("id", filter=Q(status=OrderStatus.DELIVERED)),
        )
        queryset = base
        if group == OrderStatusGroup.ACTIVE:
            queryset = queryset.exclude(status__in=INACTIVE_ORDER_STATUSES)
        elif group == OrderStatusGroup.DELIVERED:
            queryset = queryset.filter(status=OrderStatus.DELIVERED)
        queryset = queryset.order_by("-placed_at")
        if _user(request).role == Role.ADMIN:
            record_audit(
                actor=request.user,
                action="order.list",
                object_type="Order",
                object_id="*",
                request=request,
            )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return Response(
            {
                "summary": counts,
                "count": paginator.page.paginator.count,  # type: ignore[union-attr]
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": OrderListItemSerializer(page, many=True).data,
            }
        )

    @extend_schema(
        tags=["orders"],
        summary="Checkout",
        description=(
            "Requires Idempotency-Key. Throttled to 5/hour. The final payment state is "
            "set by the verified webhook; poll `GET /payments/{id}/`."
        ),
        request=OrderCreateSerializer,
        responses={201: OrderSerializer, 402: error_response("Payment declined."), **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        if _user(request).role != Role.PATIENT:
            raise PermissionDenied()
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = _user(request)
        key = request.headers.get("Idempotency-Key") or ""

        def create() -> tuple[int, dict[str, Any]]:
            order = services.place_order(
                user=user,
                data=dict(serializer.validated_data),
                idempotency_key=key,
                request=request,
            )
            order = _orders_for(user).get(pk=order.pk)
            return status.HTTP_201_CREATED, dict(OrderSerializer(order).data)

        result = execute(
            key=key,
            endpoint="orders.create",
            payload=serializer.validated_data,
            scope=str(user.pk),
            callback=create,
        )
        return Response(result.body, status=result.status_code)


class OrderDetailView(APIView):
    permission_classes = [IsPatientOrAdmin]

    @extend_schema(
        tags=["orders"],
        summary="Order detail with timeline and tracking",
        responses={200: OrderSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, order_id: str) -> Response:
        return Response(OrderSerializer(_get_order(request, order_id)).data)


class OrderPayView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["orders"],
        summary="Retry payment for an unpaid order",
        description="Requires Idempotency-Key.",
        request=PayOrderSerializer,
        responses={200: PaymentSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, order_id: str) -> Response:
        order = _get_order(request, order_id, own=True)
        serializer = PayOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key") or ""

        def pay() -> tuple[int, dict[str, Any]]:
            payment = services.pay_order(
                order=order,
                payment=dict(serializer.validated_data),
                idempotency_key=key,
                request=request,
            )
            return status.HTTP_200_OK, dict(PaymentSerializer(payment).data)

        result = execute(
            key=key,
            endpoint="orders.pay",
            payload=serializer.validated_data,
            scope=f"{request.user.pk}:{order.pk}",
            callback=pay,
        )
        return Response(result.body, status=result.status_code)


class OrderCancelView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["orders"],
        summary="Cancel an order (before compounding)",
        request=ReasonInputSerializer,
        responses={200: OrderSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, order_id: str) -> Response:
        order = _get_order(request, order_id, own=True)
        serializer = ReasonInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.cancel_order(
            order=order,
            reason=serializer.validated_data.get("reason", ""),
            actor=request.user,
            request=request,
        )
        return Response(OrderSerializer(_get_order(request, order_id, own=True)).data)


class SubscriptionListView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["subscriptions"],
        summary="My subscriptions",
        responses={200: SubscriptionSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = (
            Subscription.objects.filter(user=_user(request))
            .select_related("package", "payment_method")
            .prefetch_related("package__items")
        )
        return Response(SubscriptionSerializer(queryset, many=True).data)


class SubscriptionDetailView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["subscriptions"],
        summary="Subscription detail",
        responses={200: SubscriptionSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, subscription_id: str) -> Response:
        return Response(SubscriptionSerializer(_own_subscription(request, subscription_id)).data)


class SubscriptionPauseView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["subscriptions"],
        summary="Pause an active subscription",
        request=PauseInputSerializer,
        responses={200: SubscriptionSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, subscription_id: str) -> Response:
        subscription = _own_subscription(request, subscription_id)
        serializer = PauseInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.pause_subscription(
            subscription=subscription,
            resume_on=serializer.validated_data.get("resume_on"),
            reason=serializer.validated_data.get("reason", ""),
            request=request,
        )
        return Response(SubscriptionSerializer(_own_subscription(request, subscription_id)).data)


class SubscriptionResumeView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["subscriptions"],
        summary="Resume a paused subscription",
        request=EmptyBodySerializer,
        responses={200: SubscriptionSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, subscription_id: str) -> Response:
        subscription = _own_subscription(request, subscription_id)
        services.resume_subscription(subscription=subscription, request=request)
        return Response(SubscriptionSerializer(_own_subscription(request, subscription_id)).data)


class SubscriptionCancelView(APIView):
    permission_classes = [IsPatient]

    @extend_schema(
        tags=["subscriptions"],
        summary="Cancel at the end of the paid period",
        request=SubscriptionCancelInputSerializer,
        responses={200: SubscriptionSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, subscription_id: str) -> Response:
        subscription = _own_subscription(request, subscription_id)
        serializer = SubscriptionCancelInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.cancel_subscription(
            subscription=subscription,
            reason=serializer.validated_data.get("reason", ""),
            feedback=serializer.validated_data.get("feedback", ""),
            request=request,
        )
        return Response(SubscriptionSerializer(_own_subscription(request, subscription_id)).data)
