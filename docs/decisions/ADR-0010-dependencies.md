# ADR-0010: Dependencies added beyond the base list

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

The project brief lists the expected stack and requires a recorded reason for
anything beyond it. The following were added because the security or delivery
requirements cannot be met cleanly without them.

## Added runtime dependencies

| Package | Reason |
| --- | --- |
| `drf-spectacular-sidecar` | Serves Swagger UI/ReDoc assets offline so docs work with `DEBUG=False`, no CDN and CSP. |
| `django-csp` | Explicitly required for the Content-Security-Policy control. |
| `cryptography` | Field-level Fernet encryption (ADR-0003); also used by SimpleJWT/allauth crypto extras. |
| `argon2-cffi` | Argon2id password hashing required by the security controls. |
| `django-allauth[socialaccount]` | Google OAuth verification and social account storage. |
| `sentry-sdk[django]` | Error reporting with a required PII scrubber. |
| `structlog` | JSON logs with contextvars and redaction. |

## Added development dependencies

`django-stubs`, `djangorestframework-stubs` and `types-*` for mypy; `pre-commit`,
`bandit`, `pip-audit` for the supply-chain gates. No test-only framework beyond
pytest/factory_boy.

## Deferred dependencies

Phase 2 will add `Pillow` and `python-magic` (content-based image/PDF checks)
plus `clamd` (ClamAV client), each with an adapter/Fake. Phase 6 may add
OpenTelemetry packages if tracing is required.

## Consequences

Every added package is pinned in `pyproject.toml` and `uv.lock`, audited by
pip-audit, and scanned by bandit. New additions require a new ADR and a security
review.
