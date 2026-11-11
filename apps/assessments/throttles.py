"""Assessment-specific throttle scopes."""

from __future__ import annotations

from rest_framework.request import Request
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle
from rest_framework.views import APIView


class MethodScopedRateThrottle(ScopedRateThrottle):
    """Apply a different scope to each HTTP method on a shared path."""

    def allow_request(self, request: Request, view: APIView) -> bool:
        scopes = getattr(view, "throttle_scopes", {})
        scope = scopes.get(request.method)
        if not scope:
            return True
        self.scope = scope
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)


class AssessmentPaymentThrottle(SimpleRateThrottle):
    scope = "assessment_payment"

    def get_cache_key(self, request: Request, view: APIView) -> str | None:
        if not request.user or not request.user.is_authenticated:
            return None
        assessment_id = view.kwargs.get("assessment_id")
        if not assessment_id:
            return None
        ident = f"{request.user.pk}:{assessment_id}"
        return self.cache_format % {"scope": self.scope, "ident": ident}
