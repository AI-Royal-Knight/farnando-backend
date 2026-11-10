"""Application-level encryption for clinical free text and identifiers.

The database and bucket are encrypted at rest by the infrastructure, but
defence in depth requires that sensitive columns remain unreadable to anyone
holding only a database dump or a backup. Values are encrypted with Fernet
(AES-128-CBC + HMAC-SHA256) through ``MultiFernet`` so keys can be rotated:
the first key encrypts, every key decrypts.

Ciphertexts are prefixed with ``enc:v1:`` so legacy plaintext rows can still be
read during an expand/contract migration and so accidental double encryption is
detectable. This field intentionally does not support equality lookups.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models

PREFIX = "enc:v1:"


class EncryptionConfigurationError(RuntimeError):
    pass


def _derive_key(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


@lru_cache(maxsize=8)
def _cipher(keys: tuple[str, ...]) -> MultiFernet:
    if not keys:
        raise EncryptionConfigurationError("FIELD_ENCRYPTION_KEYS is empty")
    try:
        return MultiFernet([Fernet(key.encode("ascii")) for key in keys])
    except (ValueError, TypeError) as exc:
        raise EncryptionConfigurationError(
            "FIELD_ENCRYPTION_KEYS contains an invalid Fernet key"
        ) from exc


def get_fernet() -> MultiFernet:
    keys = tuple(str(key) for key in settings.FIELD_ENCRYPTION_KEYS)
    return _cipher(keys)


def encrypt_text(value: str) -> str:
    token = get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{PREFIX}{token}"


def decrypt_text(value: str) -> str:
    if not value.startswith(PREFIX):
        return value
    token = value[len(PREFIX) :]
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionConfigurationError(
            "Unable to decrypt field value with the configured FIELD_ENCRYPTION_KEYS"
        ) from exc


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


class EncryptedTextField(models.TextField):  # type: ignore[type-arg]
    """TextField whose values are encrypted before leaving the process."""

    description = "Encrypted text"

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> str | None:
        if value is None:
            return None
        return decrypt_text(str(value))

    def get_prep_value(self, value: Any) -> str | None:
        if value is None or value == "":
            return None if value is None else ""
        text = value if isinstance(value, str) else str(value)
        if text.startswith(PREFIX):
            # Already encrypted (for example when copying a row with update()).
            return text
        return encrypt_text(text)

    def to_python(self, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)
