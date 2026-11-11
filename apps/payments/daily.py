"""The only module that reads Daily video webhook payloads.

[VERIFY WITH PROVIDER DOCS] the signing scheme. Until confirmed: base64
HMAC-SHA256 of ``"{timestamp}.{raw_body}"`` with ``DAILY_WEBHOOK_SECRET`` in
``X-Webhook-Signature`` and the timestamp in ``X-Webhook-Timestamp``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from django.conf import settings

SIGNATURE_HEADER = "X-Webhook-Signature"
TIMESTAMP_HEADER = "X-Webhook-Timestamp"

PARTICIPANT_JOINED = "PARTICIPANT_JOINED"
MEETING_ENDED = "MEETING_ENDED"

EVENT_MAP = {
    "participant.joined": PARTICIPANT_JOINED,
    "meeting.ended": MEETING_ENDED,
}


class InvalidPayload(ValueError):
    pass


@dataclass(frozen=True)
class ParsedEvent:
    event_id: str
    event_type: str
    internal_event: str | None
    room_name: str
    user_id: str = ""


def sign(raw_body: bytes, timestamp: str, secret: str | None = None) -> str:
    key = (secret if secret is not None else settings.DAILY_WEBHOOK_SECRET).encode("utf-8")
    message = timestamp.encode("utf-8") + b"." + raw_body
    return base64.b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode("ascii")


def verify_signature(raw_body: bytes, headers: Any) -> bool:
    secret = settings.DAILY_WEBHOOK_SECRET
    provided = headers.get(SIGNATURE_HEADER, "")
    timestamp = headers.get(TIMESTAMP_HEADER, "")
    if not secret or not provided or not timestamp:
        return False
    return hmac.compare_digest(sign(raw_body, str(timestamp), secret), str(provided))


def parse(payload: Any) -> ParsedEvent:
    if not isinstance(payload, dict):
        raise InvalidPayload("body")
    event_id = str(payload.get("id") or "")
    event_type = str(payload.get("type") or "")
    if not event_id or not event_type:
        raise InvalidPayload("id/type")
    body = payload.get("payload") or {}
    if not isinstance(body, dict):
        raise InvalidPayload("payload")
    room = str(body.get("room") or body.get("room_name") or "")
    return ParsedEvent(
        event_id=event_id,
        event_type=event_type,
        internal_event=EVENT_MAP.get(event_type),
        room_name=room,
        user_id=str(body.get("user_id") or ""),
    )


__all__ = [
    "EVENT_MAP",
    "MEETING_ENDED",
    "PARTICIPANT_JOINED",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "InvalidPayload",
    "ParsedEvent",
    "parse",
    "sign",
    "verify_signature",
]
