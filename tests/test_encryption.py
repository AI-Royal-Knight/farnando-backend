"""Unit tests for encryption at rest."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from apps.core.encryption import (
    PREFIX,
    EncryptedTextField,
    EncryptionConfigurationError,
    decrypt_text,
    encrypt_text,
    generate_key,
)


def test_encrypt_decrypt_roundtrip() -> None:
    ciphertext = encrypt_text("Alergia a la penicilina")
    assert ciphertext.startswith(PREFIX)
    assert "penicilina" not in ciphertext
    assert decrypt_text(ciphertext) == "Alergia a la penicilina"


def test_plaintext_legacy_values_are_returned_unchanged() -> None:
    assert decrypt_text("not-yet-encrypted") == "not-yet-encrypted"


def test_key_rotation_uses_first_key_for_encryption() -> None:
    first = generate_key()
    second = generate_key()
    with override_settings(FIELD_ENCRYPTION_KEYS=[first]):
        ciphertext = encrypt_text("dato clínico")
    with override_settings(FIELD_ENCRYPTION_KEYS=[second, first]):
        assert decrypt_text(ciphertext) == "dato clínico"
        rotated = encrypt_text("nuevo dato")
    with (
        override_settings(FIELD_ENCRYPTION_KEYS=[first]),
        pytest.raises(EncryptionConfigurationError),
    ):
        decrypt_text(rotated)


def test_model_field_encrypts_at_the_database_boundary() -> None:
    field = EncryptedTextField()
    stored = field.get_prep_value("Historia clínica sensible")
    assert stored is not None
    assert stored.startswith(PREFIX)
    assert field.from_db_value(stored, None, None) == "Historia clínica sensible"
    assert field.get_prep_value(stored) == stored


def test_invalid_key_fails_loudly() -> None:
    with (
        override_settings(FIELD_ENCRYPTION_KEYS=[Fernet.generate_key().decode()[:-4]]),
        pytest.raises(EncryptionConfigurationError),
    ):
        encrypt_text("x")
