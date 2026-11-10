"""Shared model primitives."""

from __future__ import annotations

import uuid

from django.db import models


class BaseModel(models.Model):
    """Abstract base for every persisted domain entity.

    UUID primary keys keep identifiers non-enumerable across the public API.
    UUIDv4 is used deliberately: Django 5.2 has no native UUIDv7 field and a
    database-generated v7 would not work on SQLite during tests (see ADR-0008).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        get_latest_by = "created_at"


class IdempotencyRecord(models.Model):
    """Stored response for an Idempotency-Key replay window."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.UUIDField()
    endpoint = models.CharField(max_length=200)
    scope_hash = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["key", "endpoint", "scope_hash"],
                name="core_idempotency_unique_key_scope",
            )
        ]
        indexes = [models.Index(fields=["expires_at"], name="core_idempotency_expiry_idx")]

    def __str__(self) -> str:
        return f"{self.endpoint}:{self.key}"
