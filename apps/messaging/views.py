"""Part 3 section 8 and Part 4 section 8: messaging over REST polling."""

from __future__ import annotations

import uuid
from typing import Any, cast

from django.db.models import Q, QuerySet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.catalog.models import SiteSettings
from apps.core.audit import record_audit
from apps.core.idempotency import execute
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.schema import COMMON_ERRORS
from apps.messaging import services
from apps.messaging.models import Message, MessageThread
from apps.messaging.serializers import (
    MarkReadSerializer,
    MessageCreateSerializer,
    MessageSerializer,
    ThreadCreateSerializer,
    ThreadDetailSerializer,
    ThreadSerializer,
)
from apps.notifications.serializers import CountSerializer


class MessagePagination(CursorPagination):
    page_size = 30
    max_page_size = 100
    page_size_query_param = "page_size"
    ordering = ("-created_at", "-id")


def _user(request: Request) -> User:
    return cast(User, request.user)


class MessagingAccess(BasePermission):
    """Patients, verified physicians, nurses, and admins (read-only, audited)."""

    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role in (Role.PATIENT, Role.ADMIN):
            return True
        if user.role == Role.PHYSICIAN:
            from apps.clinical.permissions import require_verified

            require_verified(user)
            return True
        return services.is_clinician(user)


class ParticipantAccess(MessagingAccess):
    """Writers: the patient or the clinician of the thread (not admins)."""

    def has_permission(self, request: Any, view: Any) -> bool:
        if not super().has_permission(request, view):
            return False
        return bool(request.user.role != Role.ADMIN)


def _is_clinician_viewer(user: Any) -> bool:
    return bool(user.role not in (Role.PATIENT, Role.ADMIN))


def _threads_for(user: Any) -> QuerySet[MessageThread]:
    queryset = MessageThread.objects.select_related(
        "participant__clinician_profile", "patient"
    ).prefetch_related("messages")
    if user.role == Role.PATIENT:
        return queryset.filter(patient=user)
    if user.role == Role.ADMIN:
        return queryset
    if services.is_clinician(user):
        return queryset.filter(participant=user)
    return queryset.none()


def _get_thread(request: Request, thread_id: Any, *, own: bool = False) -> MessageThread:
    user = _user(request)
    queryset = _threads_for(user)
    if own:
        queryset = queryset.filter(Q(patient=user) | Q(participant=user))
    thread = queryset.filter(pk=thread_id).first()
    if thread is None:
        raise NotFound()
    if request.user.pk not in (thread.patient_id, thread.participant_id):
        record_audit(
            actor=request.user,
            action="message_thread.read",
            object_type="MessageThread",
            object_id=thread.pk,
            request=request,
        )
    return thread


def _viewer(request: Request, thread: MessageThread) -> Any:
    # Admins see unread state from the patient's point of view.
    return thread.patient_id if _user(request).role == Role.ADMIN else request.user.pk


def _safety_notice() -> str:
    return SiteSettings.load().messaging_safety_notice


def _payload(request: Request, thread: MessageThread) -> dict[str, Any]:
    if _is_clinician_viewer(_user(request)):
        return services.clinician_thread_payload(thread, viewer_id=request.user.pk)
    return services.thread_payload(thread, viewer_id=_viewer(request, thread))


def _detail(request: Request, thread: MessageThread) -> dict[str, Any]:
    data = _payload(request, thread)
    data["safety_notice"] = _safety_notice()
    if _is_clinician_viewer(_user(request)):
        data["patient_context"] = services.patient_context(thread)
    return data


class ThreadListCreateView(APIView):
    permission_classes = [MessagingAccess]

    @extend_schema(
        tags=["messages"],
        summary="My message threads (most recent first)",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR),
            OpenApiParameter("filter", OpenApiTypes.STR, enum=["ALL", "UNREAD"]),
            OpenApiParameter("page", OpenApiTypes.INT),
            OpenApiParameter("page_size", OpenApiTypes.INT),
        ],
        responses={200: ThreadSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        mode = request.query_params.get("filter", "ALL")
        if mode not in ("ALL", "UNREAD"):
            raise ValidationError({"filter": ["Must be ALL or UNREAD."]})
        queryset = _threads_for(_user(request))
        if mode == "UNREAD":
            if _user(request).role != Role.ADMIN:
                queryset = queryset.filter(
                    Q(messages__read_at__isnull=True) & ~Q(messages__sender=request.user)
                )
            else:
                queryset = queryset.filter(
                    messages__read_at__isnull=True, messages__sender_role__in=["PHYSICIAN", "NURSE"]
                )
            queryset = queryset.distinct()
        threads = sorted(
            queryset,
            key=lambda thread: thread.last_message_at or thread.created_at,
            reverse=True,
        )
        search = (request.query_params.get("search") or "").strip()
        if search:
            threads = (
                services.patient_name_matches(threads, search)
                if _is_clinician_viewer(_user(request))
                else services.threads_matching(threads, search)
            )
        if _user(request).role == Role.ADMIN:
            record_audit(
                actor=request.user,
                action="message_thread.list",
                object_type="MessageThread",
                object_id="*",
                request=request,
            )
        paginator = DefaultPageNumberPagination()
        page = paginator.paginate_queryset(cast(Any, threads), request, view=self) or []
        rows = [_payload(request, thread) for thread in page]
        return paginator.get_paginated_response(ThreadSerializer(rows, many=True).data)

    @extend_schema(
        tags=["messages"],
        summary="Get or create the thread with a care-team member",
        request=ThreadCreateSerializer,
        responses={200: ThreadDetailSerializer, 201: ThreadDetailSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        user = _user(request)
        if user.role == Role.ADMIN:
            raise PermissionDenied()
        serializer = ThreadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant_id = serializer.validated_data["participant_id"]
        if user.role == Role.PATIENT:
            thread, created = services.get_or_create_thread(
                patient=user, participant_id=participant_id
            )
        else:
            thread, created = services.get_or_create_clinician_thread(
                clinician=user, patient_id=participant_id
            )
        body = serializer.validated_data.get("body")
        if body:
            services.send_message(thread=thread, sender=user, body=body)
        thread = _get_thread(request, thread.pk)
        data = _detail(request, thread)
        return Response(
            ThreadDetailSerializer(data).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ThreadDetailView(APIView):
    permission_classes = [MessagingAccess]

    @extend_schema(
        tags=["messages"],
        summary="Thread detail with safety notice",
        responses={200: ThreadDetailSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, thread_id: str) -> Response:
        thread = _get_thread(request, thread_id)
        return Response(ThreadDetailSerializer(_detail(request, thread)).data)


class ThreadMessagesView(APIView):
    permission_classes = [MessagingAccess]

    def get_throttles(self) -> list[Any]:
        if self.request.method == "POST":
            from rest_framework.throttling import ScopedRateThrottle

            self.throttle_scope = "messaging"
            return [ScopedRateThrottle()]
        return []

    @extend_schema(
        tags=["messages"],
        summary="Messages, newest first (cursor; `after` for polling; ETag)",
        parameters=[
            OpenApiParameter("cursor", OpenApiTypes.STR),
            OpenApiParameter("page_size", OpenApiTypes.INT),
            OpenApiParameter("after", OpenApiTypes.UUID),
        ],
        responses={200: MessageSerializer(many=True), 304: None, **COMMON_ERRORS},
    )
    def get(self, request: Request, thread_id: str) -> Response:
        thread = _get_thread(request, thread_id)
        etag = services.thread_etag(thread, request.META.get("QUERY_STRING", ""))
        if request.headers.get("If-None-Match") == etag:
            response = Response(status=status.HTTP_304_NOT_MODIFIED)
            response["ETag"] = etag
            return response
        queryset = Message.objects.filter(thread=thread).select_related("sender__clinician_profile")
        after = request.query_params.get("after")
        if after:
            try:
                after_id = uuid.UUID(after)
            except ValueError as exc:
                raise ValidationError({"after": ["Must be a valid UUID."]}) from exc
            anchor = thread.messages.filter(pk=after_id).first()
            if anchor is None:
                raise ValidationError({"after": ["Unknown message."]})
            queryset = queryset.filter(created_at__gt=anchor.created_at)
        paginator = MessagePagination()
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        viewer = _viewer(request, thread)
        rows = [services.message_payload(message, viewer_id=viewer) for message in page]
        response = paginator.get_paginated_response(MessageSerializer(rows, many=True).data)
        response["ETag"] = etag
        return response

    @extend_schema(
        tags=["messages"],
        summary="Send a message",
        description="Idempotency-Key is recommended. Throttled by the `messaging` scope.",
        request=MessageCreateSerializer,
        responses={201: MessageSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, thread_id: str) -> Response:
        if _user(request).role == Role.ADMIN:
            raise PermissionDenied()
        thread = _get_thread(request, thread_id, own=True)
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = _user(request)

        def create() -> tuple[int, dict[str, Any]]:
            message = services.send_message(
                thread=thread, sender=user, body=serializer.validated_data["body"]
            )
            data = services.message_payload(message, viewer_id=user.pk)
            return status.HTTP_201_CREATED, dict(MessageSerializer(data).data)

        key = request.headers.get("Idempotency-Key")
        if not key:
            code, body = create()
            return Response(body, status=code)
        result = execute(
            key=key,
            endpoint="messages.create",
            payload=serializer.validated_data,
            scope=f"{user.pk}:{thread.pk}",
            callback=create,
        )
        return Response(result.body, status=result.status_code)


class ThreadReadView(APIView):
    permission_classes = [ParticipantAccess]

    @extend_schema(
        tags=["messages"],
        summary="Mark messages as read up to a message",
        request=MarkReadSerializer,
        responses={204: None, **COMMON_ERRORS},
    )
    def post(self, request: Request, thread_id: str) -> Response:
        thread = _get_thread(request, thread_id, own=True)
        serializer = MarkReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.mark_read(
            thread=thread,
            reader=_user(request),
            up_to_message_id=serializer.validated_data["up_to_message_id"],
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageUnreadCountView(APIView):
    permission_classes = [ParticipantAccess]

    @extend_schema(
        tags=["messages"],
        summary="Unread message count",
        responses={200: CountSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        user = _user(request)
        if user.role == Role.PATIENT:
            return Response({"count": services.patient_unread_total(user)})
        return Response({"count": services.clinician_unread_total(user)})
