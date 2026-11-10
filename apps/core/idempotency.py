"""Idempotent write support with stored responses."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.exceptions import IdempotencyConflict, IdempotencyKeyRequired
from apps.core.models import IdempotencyRecord

_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class IdempotencyResult:
    status_code: int
    body: dict[str, Any]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return _hash(canonical)


def execute(
    *,
    key: str | None,
    endpoint: str,
    payload: Any,
    scope: str,
    callback: Callable[[], tuple[int, dict[str, Any]]],
) -> IdempotencyResult:
    if not key:
        raise IdempotencyKeyRequired()
    try:
        key_uuid = uuid.UUID(str(key))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"Idempotency-Key": ["Must be a valid UUID."]}) from exc

    request_hash = _request_hash(payload)
    scope_hash = _hash(scope)
    now = timezone.now()

    with transaction.atomic():
        record = (
            IdempotencyRecord.objects.select_for_update()
            .filter(key=key_uuid, endpoint=endpoint, scope_hash=scope_hash)
            .first()
        )
        if record is not None and record.expires_at <= now:
            record.delete()
            record = None
        if record is None:
            try:
                with transaction.atomic():
                    record = IdempotencyRecord.objects.create(
                        key=key_uuid,
                        endpoint=endpoint,
                        scope_hash=scope_hash,
                        request_hash=request_hash,
                        status_code=200,
                        response_body={},
                        expires_at=now + _TTL,
                    )
            except IntegrityError:
                record = IdempotencyRecord.objects.select_for_update().get(
                    key=key_uuid, endpoint=endpoint, scope_hash=scope_hash
                )
        elif record.request_hash != request_hash:
            raise IdempotencyConflict()

        if record.request_hash != request_hash:
            raise IdempotencyConflict()

        if record.response_body and record.status_code:
            return IdempotencyResult(
                status_code=record.status_code, body=dict(record.response_body)
            )

        status_code, body = callback()
        record.status_code = status_code
        record.response_body = body
        record.save(update_fields=["status_code", "response_body"])
        return IdempotencyResult(status_code=status_code, body=body)
