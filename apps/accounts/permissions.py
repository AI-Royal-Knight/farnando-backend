"""Account-specific object/state permissions for protected clinical endpoints."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission


class IsEmailVerified(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        from apps.core.exceptions import EmailNotVerified

        user = request.user
        if not (user and user.is_authenticated and user.is_email_verified):
            raise EmailNotVerified()
        return True


class HasCurrentConsents(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        from apps.accounts.services import ConsentService
        from apps.core.exceptions import ConsentRequired

        user = request.user
        if not (user and user.is_authenticated):
            return False
        if not ConsentService.has_current_consents(user):
            raise ConsentRequired()
        return True
