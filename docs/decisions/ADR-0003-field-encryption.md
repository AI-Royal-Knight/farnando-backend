# ADR-0003: Application-level field encryption with MultiFernet

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Clinical free text and sensitive identifiers must remain unreadable in a stolen
database dump or backup, in addition to at-rest encryption provided by the
database/bucket. We need key rotation and no dependency on a KMS-specific SDK in
application code.

## Decision

Use `cryptography`'s Fernet (AES-128-CBC + HMAC-SHA256) through `MultiFernet` in
`apps.core.encryption.EncryptedTextField`.

- `FIELD_ENCRYPTION_KEYS` is a comma-separated list; the first key encrypts and
  every key decrypts.
- Ciphertexts are prefixed `enc:v1:` so legacy plaintext can be read during an
  expand/contract migration and accidental double encryption is detectable.
- Production validates the keys at startup and fails fast.
- Encrypted columns are not queryable by equality or ordering.

## Consequences

- Rotation is a two-step deploy: prepend the new key, re-encrypt rows in a
  background task, then remove the old key.
- Searching clinical free text in the database is not possible; reports use
  aggregate/non-encrypted fields or application-side processing with strict
  access controls.
- Key loss is unrecoverable. Keys must be backed up in the secret manager with
  break-glass access and tested restore.
- Database/bucket encryption at rest and TLS remain required; this is defence in
  depth, not a replacement.
