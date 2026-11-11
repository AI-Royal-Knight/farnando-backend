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
Part 2 adds `payment_failed` (402), `assessment_locked` (409),
`assessment_incomplete` (409, with `details.missing_steps`),
`upload_limit_reached` (409), `no_lab_files` (409), `target_weight_invalid`
(400), `target_below_healthy_range` (400) and `under_age` (400).

## Status codes

| Code | Meaning |
| --- | --- |
| 200 | Read/update success |
| 201 | Resource created |
| 202 | Accepted (asynchronous operations) |
| 204 | No content (logout, password reset confirm) |
| 400 | Validation |
| 401 | Missing/invalid credentials or token |
| 402 | Payment declined (`payment_failed`) |
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
- assessment creation and submission (Part 2)
- evaluation fee payment (Part 2)
- order creation and payment intent creation (Part 4)

The key is scoped to the caller + endpoint + request hash. Replaying the same
key and body returns the stored status/body; reusing a key with a different body
returns 409. Keys expire after 24 hours. The service is implemented in
`apps/core/idempotency.py` and is used by the complaints book, assessment
create/submit and fee payment.

## Dates, time and money

- Timestamps are ISO-8601 UTC with a trailing `Z`, e.g. `2026-11-10T14:00:00Z`;
  dates are `YYYY-MM-DD`.
- Business timezone is `America/Lima`.
- Money is always an object with a decimal string and ISO 4217 code
  (`{"amount": "129.90", "currency": "PEN"}`). Floats are forbidden.
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


`POST /api/v1/consents/` accepts
`{"consents": [{"document_type": "TERMS_OF_SERVICE", "version": "..."}]}`.
The client sends the version it displayed; the server accepts only the current
version and records the timestamp, IP and user agent. Re-accepting the same
version is idempotent. Clients cannot record an outdated version.

## Assessment and evaluation endpoints (Part 2)

All routes are owner-scoped for patients; objects outside scope return 404.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/programs/` | Active programs with `is_available` and `position` |
| GET/POST | `/assessments/` | List (`?status=`, page-number) / create or resume; `Idempotency-Key` required on POST |
| GET | `/assessments/{id}/` | Detail, answers, fee, journey, `current_step_key` |
| GET | `/assessments/{id}/questionnaire/` | Pinned questionnaire version; never exposes screening effects |
| PUT | `/assessments/{id}/measurements/` | Canonical kg/cm plus display units; BMI computed server-side |
| PUT | `/assessments/{id}/steps/{step_key}/` | Upsert one `QUESTIONS` step; hidden answers are discarded |
| GET | `/assessments/{id}/summary/` | 409 `assessment_incomplete` with `details.missing_steps` |
| POST | `/assessments/{id}/submit/` | `Idempotency-Key`; returns `next` |
| POST | `/assessments/{id}/request-review/` | Only from `SCREENED_OUT`, once |
| POST | `/assessments/{id}/cancel/` | Optional `reason` |
| GET | `/assessments/{id}/fee/` | Amount is `{amount, currency}`; latest payment |
| POST | `/assessments/{id}/fee/pay/` | `Idempotency-Key`; synchronous success stays `PENDING` |
| GET | `/assessments/{id}/lab-recommendations/` | Config or physician snapshot |
| GET/POST | `/lab-uploads/` | List with `?assessment_id=`, or start a pre-signed upload |
| GET/DELETE | `/lab-uploads/{id}/` | Detail / soft delete |
| POST | `/lab-uploads/{id}/confirm/` | Verifies object, queues scan |
| GET | `/lab-uploads/{id}/download/` | Only `CLEAN`; audited |
| POST | `/assessments/{id}/labs/submit/` | 409 `no_lab_files` when empty |
| POST | `/assessments/{id}/labs/skip/` | Sets `DEFERRED`; once |
| GET | `/assessments/{id}/evaluation/` | 404 for a draft |
| GET | `/assessments/{id}/evaluation/prescription/download/` | Only `APPROVED` with a stored PDF; audited |

Measurement request:

```json
{
  "current_weight": {"value": "180.8", "unit": "LB"},
  "target_weight": {"value": "154.3", "unit": "LB"},
  "height": {"value": "68.9", "unit": "IN"},
  "sex": "MALE",
  "date_of_birth": "1990-01-01"
}
```

The response always includes `kg`/`cm` and `display: {value, unit}`; BMI,
category, target BMI and the weight goal are read-only. `sex` is `MALE`,
`FEMALE` or `OTHER`.

Fee response excerpt:

```json
{
  "status": "PENDING",
  "amount": {"amount": "25.00", "currency": "USD"},
  "is_waived": false,
  "payment_methods": [{"code": "CULQI", "label": "Culqi"}],
  "payment": null
}
```

`POST .../fee/pay/` accepts `{"payment_method": "CULQI", "provider_token": "..."}`.
A decline is `402 payment_failed`; the final success state is set by the Part 3
verified webhook.

## Throttling

Limits are scoped: `auth` 5/min, `password_reset` 3/hour,
`email_verification` 3/hour, `assessment_create` 20/hour,
`assessment_measurements` 120/min, `assessment_submit` 5/hour,
`assessment_payment` 5/hour, `lab_upload` 20/hour, `messaging` 60/min,
anonymous 100/day, authenticated 1000/day. Exceeding a limit returns 429 with
the standard envelope. django-axes lockouts also return 429.

## Security headers

- All responses: `X-Request-ID`.
- Production: HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  referrer policy, CSP, secure cookies.
- CORS is an explicit allow-list; credentials are disabled because the SPA uses
  bearer tokens.

## Uploads (Part 2)

Two-step flow: `POST /lab-uploads/` returns a pre-signed `PUT` URL, the client
uploads directly to the private bucket, then calls `POST /lab-uploads/{id}/confirm/`.
Direct multipart upload is not exposed for clinical files. Accepted types:
PDF, PNG, JPEG by magic bytes; 10 MB max; 5 active uploads per assessment; files
remain invisible to physicians until the antivirus scan is `CLEAN`. Upload URLs
expire after 15 minutes; download URLs after 5 minutes. Every download is
audited. With `FileSystemStorage` (tests/local only) the helper returns a media
URL rather than a real pre-signed `PUT`; production must use S3-compatible
private storage.

## Patient portal (Part 3)

- Instants are rendered and parsed as UTC with a trailing `Z`. Responses that
  show a schedule also carry an IANA `timezone`.
- Writes that move money or hold a slot require `Idempotency-Key`:
  `POST /consultations/`, `.../reschedule/`, `POST /orders/`,
  `POST /orders/{id}/pay/`. Sending a message accepts it optionally.
- `GET /physicians/{id}/availability/` is never cached (`Cache-Control:
  no-store`); `GET /geo/ubigeo/` is cacheable for a day.
- `GET /orders/` adds a `summary` object next to the usual page fields.
- Messages and notifications use cursor pagination (`cursor`, `page_size`).
  `GET /message-threads/{id}/messages/?after=<id>` returns only newer messages
  and sends an `ETag`; `If-None-Match` returns `304` when nothing changed.
- Throttles: `consultation_create` 10/hour, `order_create` 5/hour, `messaging`
  60/min (sending). Webhooks are not throttled.
- New error codes are listed in `docs/contract-deviations.md` item 72.

## Clinician side (Part 4)

- Evaluation decisions (`release`, `request-labs`, `reject`, `approve`) need
  `If-Match: "<version>"` (`428` when missing, `412` when stale); responses send
  the new version in `ETag`. Decisions also need `Idempotency-Key`.
- `POST /auth/login/` may return `{mfa_required, mfa_token, methods}`; finish
  with `POST /auth/mfa/verify/`.
- `/clinical/*` returns `403 credentials_not_verified` until staff verify the
  physician.

## Messaging (Phase 5)

REST first with cursor pagination and `since` polling/ETag. The data model is
channel-ready so Django Channels can be added later without schema changes (ADR
0007). Polling intervals should be ≥ 3 seconds; conditional requests avoid
re-downloading unchanged threads.
