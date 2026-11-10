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
  "access": "<jwt>",
  "refresh": "<jwt>",
  "token_type": "Bearer",
  "expires_in": 900,
  "user": {"id": "...", "email": "...", "role": "PATIENT", "is_email_verified": true}
}
```

Send `Authorization: Bearer <access>`. Refresh with
`POST /api/v1/auth/token/refresh/` (rotates and blacklists the old refresh).
Logout blacklists the refresh token and is idempotent.

Errors never reveal whether an account exists. Registration and password reset
always return 202.

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

Common codes: `validation_error`, `authentication_required`,
`authentication_failed`, `permission_denied`, `not_found`,
`method_not_allowed`, `conflict`, `throttled`, `account_locked`,
`service_unavailable`, `server_error`.

## Status codes

| Code | Meaning |
| --- | --- |
| 200 | Read/update success |
| 201 | Resource created |
| 202 | Accepted (enumeration-resistant registration/reset) |
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

- order creation (Phase 4)
- payment intent creation (Phase 4)
- assessment submission (Phase 2)

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

## Consents

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
