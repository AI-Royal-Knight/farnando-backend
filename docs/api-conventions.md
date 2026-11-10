# API conventions

## Base URL and versioning

- Base path: `/api/v1/`.
- Breaking changes require a new version path; additive fields are safe.
- JSON only. Requests use `Content-Type: application/json` unless an upload
  endpoint documents multipart.
- OpenAPI 3.1: `/api/schema/`, Swagger UI `/api/docs/`, ReDoc `/api/redoc/`.
  Docs are disabled by default in production and staff-only when enabled.

## Authentication

```http
POST /api/v1/auth/login/
Content-Type: application/json

{"email": "paciente@example.com", "password": "..."}
```

Response:

```json
{
  "user": {
    "id": "...",
    "email": "...",
    "full_name": "Ana Quispe",
    "role": "PATIENT",
    "is_email_verified": true,
    "auth_providers": ["PASSWORD"],
    "assessment_status": null,
    "created_at": "2026-11-10T14:30:00Z"
  },
  "tokens": {"access": "<jwt>", "refresh": "<jwt>"}
}
```

Send `Authorization: Bearer <access>`. Refresh with
`POST /api/v1/auth/token/refresh/` (rotates and blacklists the old refresh).
Logout blacklists the refresh token and is idempotent.

Password reset always returns 204. Registration returns 201 on success and a
generic 400 when the email already exists (the contract requires this, although
it is observable; see docs/contract-deviations.md).

## Error envelope

Every API error:

```json
{
  "code": "validation_error",
  "message": "Validation failed.",
  "details": {"email": ["Enter a valid email address."]},
  "request_id": "f4d3..."
}
```

- `code` is stable and machine-readable.
- `details` is `{}` when there is nothing safe to add.
- `request_id` matches the `X-Request-ID` response header. Clients may send
  `X-Request-ID`; unsafe values are replaced.
- Validation errors use `validation_error`; individual field errors remain in
  `details`.
- Authentication failures are 401; authorization failures are 403. Objects
  outside the caller's scope are returned as **404**.
- Unknown `/api/` paths and unhandled API 500s use the same envelope.
- No exception message, SQL, stack trace or PHI is returned.

Common codes: `validation_error`, `unknown_field`, `idempotency_key_required`,
`not_authenticated`, `token_invalid`, `token_expired`, `invalid_credentials`,
`account_disabled`, `permission_denied`, `email_not_verified`,
`consent_required`, `not_found`, `method_not_allowed`, `conflict`,
`invalid_state_transition`, `idempotency_conflict`, `file_too_large`,
`unsupported_media_type`, `throttled`, `account_locked`, `server_error`.

## Status codes

| Code | Meaning |
| --- | --- |
| 200 | Read/update success |
| 201 | Resource created |
| 202 | Accepted (asynchronous operations) |
| 204 | No content (logout, password reset confirm) |
| 400 | Validation |
| 401 | Missing/invalid credentials or token |
| 403 | Authenticated but not permitted |
| 404 | Not found or outside scope |
| 409 | Conflict/state transition/idempotency reuse |
| 415 | Unsupported media type |
| 429 | Throttle or brute-force lockout |
| 503 | Dependency unavailable |

## Pagination

- Page-number pagination for admin tables: `?page=2&page_size=50` (default 20,
  max 100). Response has `count`, `next`, `previous`, `results`.
- Cursor pagination for feeds/messages (Phase 5): `?cursor=...&page_size=...`,
  stable under inserts.

## Filtering, search and ordering

List endpoints document supported query parameters. django-filter handles exact
filters and ranges; `search` is a full-text/`icontains` search; `ordering` uses
an allow-list per view (e.g. `?ordering=-created_at`). Never expose arbitrary
field ordering.

## Idempotency

`Idempotency-Key` is required on:

- public complaints-book creation (Part 1)
- assessment submission (Part 2)
- order creation and payment intent creation (Part 4)

The key is scoped to the authenticated user + endpoint + request hash. Replaying
the same key and body returns the stored status/body; reusing a key with a
different body returns 409. Keys expire after the configured TTL. The service
implementation arrives with the first endpoint that needs it; no placeholder
endpoint is exposed.

## Dates, time and money

- Timestamps are ISO-8601 UTC with offset, e.g. `2026-11-10T14:00:00Z`.
- Business timezone is `America/Lima`.
- Money is integer minor units or a decimal string plus ISO currency
  (`{"amount": "129.90", "currency": "PEN"}`); floats are forbidden.
- Measurements are stored in kg/cm. Display units are never trusted for
  clinical calculations.

## Public content and legal documents

`GET /legal-documents/` and `GET /legal-documents/{slug}/` return the current
published version. `GET /consents/me/` returns the authenticated user's history;
`POST /consents/` accepts `{"consents": [{"document_type": "...", "version": "..."}]}`
and only current versions are accepted.

Public content endpoints (`/public/*`) are anonymous and return
`Cache-Control: public, max-age=300`. Contact and complaints-book submissions
are throttled per IP and, where applicable, per email.


`POST /api/v1/auth/consents/` accepts `{"document_type": "TERMS|PRIVACY|HEALTH_DATA"}`.
The server records the **current configured version**, timestamp, IP and user
agent. Re-accepting the same version is idempotent. Clients cannot select an
outdated version.

## Throttling

Limits are scoped: `auth` 5/min, `password_reset` 3/hour,
`email_verification` 3/hour, `upload` 20/hour, `messaging` 60/min,
anonymous 100/day, authenticated 1000/day. Exceeding a limit returns 429 with
the standard envelope. django-axes lockouts also return 429.

## Security headers

- All responses: `X-Request-ID`.
- Production: HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  referrer policy, CSP, secure cookies.
- CORS is an explicit allow-list; credentials are disabled because the SPA uses
  bearer tokens.

## Uploads (Phase 2)

Two-step flow: request a pre-signed upload URL, upload directly to the private
bucket, then confirm. Direct multipart upload is not exposed for clinical files.
Accepted types: PDF, PNG, JPEG by magic bytes; 10 MB max; files remain invisible
to physicians until the antivirus scan is `CLEAN`.

## Messaging (Phase 5)

REST first with cursor pagination and `since` polling/ETag. The data model is
channel-ready so Django Channels can be added later without schema changes (ADR
0007). Polling intervals should be ≥ 3 seconds; conditional requests avoid
re-downloading unchanged threads.
