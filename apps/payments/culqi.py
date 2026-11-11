"""The only module that reads Culqi webhook payloads.

[VERIFY WITH CULQI DOCS] the signature header, signing scheme and event names.
Until confirmed, requests are signed with HMAC-SHA256 over the raw body using
``CULQI_WEBHOOK_SECRET`` and sent in ``X-Culqi-Signature`` (hex digest).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings

SIGNATURE_HEADER = "X-Culqi-Signature"

CHARGE_SUCCEEDED = "CHARGE_SUCCEEDED"
CHARGE_FAILED = "CHARGE_FAILED"
CHARGE_REQUIRES_ACTION = "CHARGE_REQUIRES_ACTION"
REFUND_SUCCEEDED = "REFUND_SUCCEEDED"

EVENT_MAP = {
    "charge.creation.succeeded": CHARGE_SUCCEEDED,
    "charge.succeeded": CHARGE_SUCCEEDED,
    "charge.creation.failed": CHARGE_FAILED,
    "charge.failed": CHARGE_FAILED,
    "charge.requires_action": CHARGE_REQUIRES_ACTION,
    "charge.3ds.required": CHARGE_REQUIRES_ACTION,
    "refund.creation.succeeded": REFUND_SUCCEEDED,
    "refund.succeeded": REFUND_SUCCEEDED,
}


class InvalidPayload(ValueError):
    pass


@dataclass(frozen=True)
class ParsedEvent:
    event_id: str
    event_type: str
    internal_event: str | None
    charge_id: str
    amount: Decimal | None
    refunded_amount: Decimal | None
    failure_code: str
    action: dict[str, str] | None


def sign(raw_body: bytes, secret: str | None = None) -> str:
    key = (secret if secret is not None else settings.CULQI_WEBHOOK_SECRET).encode("utf-8")
    return hmac.new(key, raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, headers: Any) -> bool:
    secret = settings.CULQI_WEBHOOK_SECRET
    provided = headers.get(SIGNATURE_HEADER, "")
    if not secret or not provided:
        return False
    return hmac.compare_digest(sign(raw_body, secret), str(provided))


def _cents(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return (Decimal(str(value)) / Decimal(100)).quantize(Decimal("0.01"))
    except Exception as exc:
        raise InvalidPayload("amount") from exc


def parse(payload: Any) -> ParsedEvent:
    if not isinstance(payload, dict):
        raise InvalidPayload("body")
    event_id = str(payload.get("id") or "")
    event_type = str(payload.get("type") or "")
    if not event_id or not event_type:
        raise InvalidPayload("id/type")
    data = payload.get("data") or {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise InvalidPayload("data") from exc
    if not isinstance(data, dict):
        raise InvalidPayload("data")
    internal = EVENT_MAP.get(event_type)
    if internal == REFUND_SUCCEEDED:
        charge_id = str(data.get("charge_id") or "")
        refunded = _cents(data.get("amount_refunded", data.get("amount")))
        amount = None
    else:
        charge_id = str(data.get("id") or "")
        amount = _cents(data.get("amount"))
        refunded = None
    raw_outcome = data.get("outcome")
    outcome: dict[str, Any] = raw_outcome if isinstance(raw_outcome, dict) else {}
    action_url = data.get("action_url") or data.get("redirect_url")
    return ParsedEvent(
        event_id=event_id,
        event_type=event_type,
        internal_event=internal,
        charge_id=charge_id,
        amount=amount,
        refunded_amount=refunded,
        failure_code=str(outcome.get("code") or data.get("failure_code") or "")[:120],
        action={"type": "REDIRECT", "url": str(action_url)} if action_url else None,
    )


__all__ = [
    "CHARGE_FAILED",
    "CHARGE_REQUIRES_ACTION",
    "CHARGE_SUCCEEDED",
    "EVENT_MAP",
    "REFUND_SUCCEEDED",
    "SIGNATURE_HEADER",
    "InvalidPayload",
    "ParsedEvent",
    "parse",
    "sign",
    "verify_signature",
]
