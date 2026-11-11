"""Part 3 section 9: in-app notifications for any authenticated user."""

from __future__ import annotations

from typing import cast

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.serializers import EmptyBodySerializer
from apps.core.permissions import IsAuthenticatedAndActive
from apps.core.schema import COMMON_ERRORS
from apps.notifications.models import Notification
from apps.notifications.serializers import CountSerializer, NotificationSerializer
from apps.notifications.services import unread_count


def _user(request: Request) -> User:
    return cast(User, request.user)


class NotificationPagination(CursorPagination):
    page_size = 20
    max_page_size = 50
    page_size_query_param = "page_size"
    ordering = "-created_at"


def _parse_bool(value: str | None, field: str) -> bool | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in ("true", "1"):
        return True
    if lowered in ("false", "0"):
        return False
    raise ValidationError({field: ["Must be true or false."]})


class NotificationListView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["notifications"],
        summary="List my notifications (newest first)",
        parameters=[
            OpenApiParameter("unread", OpenApiTypes.BOOL),
            OpenApiParameter("cursor", OpenApiTypes.STR),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: NotificationSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = Notification.objects.filter(user=_user(request))
        unread = _parse_bool(request.query_params.get("unread"), "unread")
        if unread is not None:
            queryset = queryset.filter(is_read=not unread)
        paginator = NotificationPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(NotificationSerializer(page, many=True).data)


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["notifications"],
        summary="Mark one notification as read",
        request=EmptyBodySerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request, notification_id: str) -> Response:
        notification = Notification.objects.filter(pk=notification_id, user=_user(request)).first()
        if notification is None:
            raise NotFound()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["notifications"],
        summary="Mark all my notifications as read",
        request=EmptyBodySerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        now = timezone.now()
        Notification.objects.filter(user=_user(request), is_read=False).update(
            is_read=True, read_at=now, updated_at=now
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationUnreadCountView(APIView):
    permission_classes = [IsAuthenticatedAndActive]

    @extend_schema(
        tags=["notifications"],
        summary="Unread notification count",
        responses={200: CountSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return Response({"count": unread_count(_user(request))})
