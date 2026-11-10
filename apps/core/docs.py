"""Authenticated, feature-flagged API documentation views."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.http import Http404
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.core.permissions import CanAccessApiDocs


class DocsEnabledMixin:
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        if not settings.ENABLE_API_DOCS:
            raise Http404
        super().initial(request, *args, **kwargs)  # type: ignore[misc]


class ApiSchemaView(DocsEnabledMixin, SpectacularAPIView):
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [CanAccessApiDocs]


class ApiSwaggerView(DocsEnabledMixin, SpectacularSwaggerView):
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [CanAccessApiDocs]


class ApiRedocView(DocsEnabledMixin, SpectacularRedocView):
    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [CanAccessApiDocs]
