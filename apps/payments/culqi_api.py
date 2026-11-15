"""Server-side calls to the Culqi REST API (v2): charges, refunds, customers and cards.

Card data never reaches this server: the browser tokenizes the card with the public key
(``secure.culqi.com/v2/tokens``) and only the single-use token id is sent here. Requests
are authenticated with ``CULQI_SECRET_KEY``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import structlog
from django.conf import settings

from apps.core.exceptions import ServiceUnavailable

logger = structlog.get_logger(__name__)

#: ``outcome.type`` of an approved charge.
APPROVED = "venta_exitosa"


class CulqiError(Exception):
    """Culqi answered with an error object (declines, invalid tokens, duplicates)."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("merchant_message") or body.get("type") or "culqi_error")
        self.status = status
        self.body = body

    @property
    def type(self) -> str:
        return str(self.body.get("type") or "")

    @property
    def code(self) -> str:
        """Short decline code (``insufficient_funds``, ``stolen_card``…), at most 120 chars."""
        value = self.body.get("decline_code") or self.body.get("code") or self.type
        return str(value or "")[:120]

    @property
    def user_message(self) -> str:
        return str(self.body.get("user_message") or self.body.get("merchant_message") or "")


def to_cents(amount: Decimal) -> int:
    return int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call Culqi and return the JSON body. Raises ``CulqiError`` for 4xx answers."""

    secret = str(getattr(settings, "CULQI_SECRET_KEY", "") or "")
    if not secret:
        raise ServiceUnavailable("Culqi is not configured in this environment.")
    base = str(settings.CULQI_API_URL).rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - fixed HTTPS endpoint from settings
        f"{base}/{path.lstrip('/')}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.CULQI_TIMEOUT) as response:  # noqa: S310  # nosec B310
            return dict(json.loads(response.read() or b"{}"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        if 400 <= exc.code < 500 and exc.code != 401 and isinstance(payload, dict):
            raise CulqiError(exc.code, payload) from exc
        logger.warning("culqi_http_error", status=exc.code, path=path)
        raise ServiceUnavailable("The payment provider is unavailable.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("culqi_unreachable", path=path, error=str(exc))
        raise ServiceUnavailable("The payment provider is unavailable.") from exc


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")
