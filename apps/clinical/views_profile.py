"""Part 4 section 2: invitations, specialties, avatars, MFA-aware profile."""

from __future__ import annotations

from typing import Any, cast

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated, NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.mfa import mfa_required
from apps.accounts.models import User
from apps.accounts.serializers import UserSummarySerializer
from apps.accounts.services import issue_tokens
from apps.clinical import onboarding
from apps.clinical.models import AvatarUpload, PhysicianInvitation, Specialty
from apps.clinical.permissions import IsPhysician, physician_profile
from apps.clinical.serializers_profile import (
    AcceptResponseSerializer,
    AvatarConfirmInputSerializer,
    AvatarReadySerializer,
    AvatarUploadCreateSerializer,
    InvitationAcceptSerializer,
    InvitationSerializer,
    PhysicianProfilePatchSerializer,
    PhysicianProfileSerializer,
    SpecialtySerializer,
    UploadInitSerializer,
)
from apps.core.schema import COMMON_ERRORS
from apps.core.storage import presigned_url


class InvitationDetailView(APIView):
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "invitation_lookup"

    @extend_schema(
        tags=["physicians"],
        summary="Validate a physician invitation link",
        auth=[],
        responses={200: InvitationSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request, token: str) -> Response:
        invitation = onboarding.resolve_invitation(token)
        inviter = invitation.invited_by
        body = {
            "email": invitation.email,
            "status": invitation.status,
            "expires_at": invitation.expires_at,
            "invited_by_name": inviter.get_full_name() if inviter else None,
        }
        return Response(InvitationSerializer(body).data)


class InvitationAcceptView(APIView):
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "invitation_accept"

    @extend_schema(
        tags=["physicians"],
        summary="Create a physician account from an invitation",
        auth=[],
        request=InvitationAcceptSerializer,
        responses={201: AcceptResponseSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, token: str) -> Response:
        onboarding.resolve_invitation(token)
        serializer = InvitationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = onboarding.accept_invitation(
            token=token, data=dict(serializer.validated_data), request=request
        )
        body = {
            "user": UserSummarySerializer(user).data,
            "tokens": issue_tokens(user),
            "mfa_enrollment_required": mfa_required(user),
        }
        return Response(body, status=status.HTTP_201_CREATED)


class SpecialtyListView(APIView):
    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["physicians"],
        summary="Specialty options",
        auth=[],
        responses={200: SpecialtySerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        rows = [
            {
                "id": item.pk,
                "name": item.name,
                "requires_registration_number": item.requires_registration_number,
            }
            for item in Specialty.objects.filter(is_active=True)
        ]
        return Response(SpecialtySerializer(rows, many=True).data)


def _invitation_from(token: str | None) -> PhysicianInvitation | None:
    if not token:
        return None
    return onboarding.resolve_invitation(token)


class AvatarUploadCreateView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "avatar_upload"

    @extend_schema(
        tags=["physicians"],
        summary="Start a pre-signed avatar upload",
        request=AvatarUploadCreateSerializer,
        responses={201: UploadInitSerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request) -> Response:
        serializer = AvatarUploadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user if isinstance(request.user, User) else None
        invitation = _invitation_from(serializer.validated_data.get("invitation_token"))
        if user is None and invitation is None:
            raise NotAuthenticated()
        upload, target = onboarding.start_avatar_upload(
            user=user, invitation=invitation, data=dict(serializer.validated_data)
        )
        return Response(
            UploadInitSerializer({"id": upload.pk, "upload": target}).data,
            status=status.HTTP_201_CREATED,
        )


class AvatarUploadConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "avatar_upload"

    @extend_schema(
        tags=["physicians"],
        summary="Validate and re-encode an uploaded avatar",
        request=AvatarConfirmInputSerializer,
        responses={200: AvatarReadySerializer, **COMMON_ERRORS},
    )
    def post(self, request: Request, upload_id: str) -> Response:
        serializer = AvatarConfirmInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        queryset = AvatarUpload.objects.filter(pk=upload_id)
        if request.user.is_authenticated:
            upload = queryset.filter(owner=request.user).first()
        else:
            invitation = _invitation_from(serializer.validated_data.get("invitation_token"))
            if invitation is None:
                raise NotAuthenticated()
            upload = queryset.filter(invitation=invitation, owner__isnull=True).first()
        if upload is None:
            raise NotFound()
        upload = onboarding.confirm_avatar(upload)
        body = {
            "id": upload.pk,
            "status": upload.status,
            "preview_url": presigned_url(upload.storage_key, expires_in=300),
        }
        return Response(AvatarReadySerializer(body).data)


class PhysicianMeView(APIView):
    permission_classes = [IsPhysician]

    @extend_schema(
        tags=["physicians"],
        summary="My physician profile",
        responses={200: PhysicianProfileSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return Response(PhysicianProfileSerializer(physician_profile(request.user)).data)

    @extend_schema(
        tags=["physicians"],
        summary="Update my physician profile",
        request=PhysicianProfilePatchSerializer,
        responses={200: PhysicianProfileSerializer, **COMMON_ERRORS},
    )
    def patch(self, request: Request) -> Response:
        if not isinstance(request.data, dict):
            raise ValidationError({"non_field_errors": ["Expected an object."]})
        user = cast(User, request.user)
        profile = onboarding.update_profile(user, physician_profile(user), dict(request.data))
        return Response(PhysicianProfileSerializer(profile).data)
