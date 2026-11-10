"""Per-email contact throttle in addition to the per-IP scope."""

from __future__ import annotations

import hashlib

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView


class ContactEmailThrottle(SimpleRateThrottle):
    scope = "contact_email"

    def get_cache_key(self, request: Request, view: APIView) -> str | None:
        data = request.data if hasattr(request.data, "get") else {}
        email = str(data.get("email", "")).strip().lower()
        if not email:
            return None
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}
