# Security and privacy

This document describes controls that are implemented in Phase 1 and controls
that are required in later phases. Anything requiring Peruvian legal or clinical
review is marked **LEGAL_REVIEW** or **CLINICAL_REVIEW**; the engineering team
does not invent those rules.

## 1. Threat model (summary)

| Threat | Impact | Primary controls |
| --- | --- | --- |
| Account takeover / credential stuffing | Clinical data disclosure | Argon2, 12-char minimum, django-axes lockout, scoped throttles, MFA later |
| Broken object-level authorization | Cross-patient record access | Selector scoping, explicit permissions, 404 outside scope, role matrix tests |
| Token theft through XSS | Session hijack | Short access TTL, CSP, minimal token storage, no PHI in browser storage, rotation + blacklist |
| Mass assignment | Privilege escalation | Read-only role/ownership fields, service-layer writes, no client status/price/BMI |
| PHI in logs/Sentry/Celery | Privacy breach | Redaction processor, Sentry scrubber, id-only task args, no request-body logging |
| Malicious upload | Malware / stored XSS | Content magic-byte allow-list, 10 MB cap, random keys, private bucket, ClamAV scan gate |
| Payment webhook forgery/replay | Financial loss | Signature verification, raw event storage, unique event id, idempotent handling |
| Database/backup theft | Health-data breach | Encryption at rest, field encryption, TLS, least-privilege DB users, secret manager |
| Supply-chain compromise | Full compromise | Pinned lockfile, pip-audit, bandit, gitleaks, Dependabot, non-root image |

## 2. Authentication

- Passwords are hashed with **Argon2id** (`Argon2PasswordHasher` first). PBKDF2
  and scrypt remain as fallback verifiers for imported hashes.
- Django password validators: user-attribute similarity, minimum length 12,
  common-password list, all-numeric rejection. Password strength is validated
  server-side, including admin-created users.
- Login accepts email (case-insensitive, normalised to lowercase). Unknown,
  inactive and wrong-password accounts return the same 401 message.
- Password reset does not disclose account existence (always 204). Registration
  follows the owner-approved contract: 201 on success and a generic 400 for an
  existing address, with an account-exists email. This is observable by design;
  the trade-off is recorded in `docs/contract-deviations.md`.
- Email verification is mandatory before any assessment submission (Phase 2
  permission) and is expressed by `is_email_verified`.
- Google sign-in verifies the Google ID token with django-allauth. An email is
  only linked when Google marks it verified; otherwise login is rejected.
- **LEGAL_REVIEW:** whether physicians/staff require MFA is a policy decision.
  TOTP/WebAuthn can be added with django-allauth MFA without changing the user
  schema.

## 3. Authorization

- Global default permission is authenticated + active.
- Coarse roles: `PATIENT`, `PHYSICIAN`, `STAFF`, `ADMIN` via reusable
  `HasRole` subclasses.
- Object scoping is performed in selectors. A patient filtering by another
  patient's UUID gets 404, not 403. A physician only sees assigned patients and
  evaluations; staff permissions are explicit; admin is full-access.
- Serializers mark role, ownership, price, status, BMI and computed fields
  read-only. Updates go through services with explicit allowed transitions.
- A role × endpoint matrix test is required in Phase 2 and grows with every
  endpoint. List endpoints must have query-count tests.
- Staff/admin Django admin access requires `is_staff`; the API uses roles and
  `is_staff` only for docs/admin.

## 4. Tokens and sessions

- Access JWT: 15 minutes, HS256, 30s leeway.
- Refresh JWT: 7 days, rotating, blacklisted after rotation. Logout and password
  reset blacklist outstanding refresh tokens.
- `UPDATE_LAST_LOGIN=True`; user id is a UUID claim; role/verification claims are
  convenience only and are never used for authorization decisions.
- The SPA uses bearer tokens (ADR-0002). Access and refresh tokens must not be
  written to `localStorage`; keep them in memory or platform secure storage and
  re-authenticate after a reload. Cookies are used only for Django admin and
  docs sessions; production cookies are `Secure`, `HttpOnly`, `SameSite=Lax`.
- **Residual risk:** a stolen access token is valid for up to 15 minutes because
  JWTs are stateless. Sensitive account changes require a fresh login in later
  phases.

## 5. Brute force and abuse

- django-axes: 5 failures per username+IP, 15-minute cool-off, reset on success.
  Lockouts return 429 with the standard error envelope.
- Scoped DRF throttles:

| Scope | Default |
| --- | --- |
| anonymous | 100/day |
| authenticated user | 1000/day |
| `auth` | 5/min |
| `password_reset` | 3/hour |
| `email_verification` | 3/hour |
| `upload` | 20/hour (Phase 2) |
| `messaging` | 60/min (Phase 5) |

Rates are configurable by environment. Throttle keys include the user id when
authenticated and the client IP otherwise.

## 6. Transport and browser hardening

Production settings enforce:

- `SECURE_SSL_REDIRECT=True`, `SECURE_PROXY_SSL_HEADER` and HSTS for one year
  with `includeSubDomains` and `preload`.
- `Secure` + `HttpOnly` session/CSRF cookies; `X_FRAME_OPTIONS=DENY`;
  `SECURE_CONTENT_TYPE_NOSNIFF`; strict referrer policy; same-origin opener
  policy.
- Explicit `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and CORS allow-list. Wildcard
  hosts are rejected in production.
- CSP denies objects/frames by default, restricts scripts/styles/fonts/images/
  connections to self (data: only for images/fonts). Swagger UI gets
  `'unsafe-inline'` only on its own responses and only when docs are enabled.
  Django admin gets the same narrow relaxation via path-scoped middleware.
- API docs return 404 when `ENABLE_API_DOCS=False`. When enabled they require a
  staff/admin authenticated session (or JWT).
- `python manage.py check --deploy` must report zero warnings in CI.

## 7. Public forms

Contact and complaints-book endpoints require a captcha in production. The
verifier is a configurable port; production refuses to start if CAPTCHA is
required but only the no-op verifier is configured. The complaints-book number
is generated from a locked per-year sequence inside the same transaction as the
record, and `Idempotency-Key` replay is stored so duplicate submissions do not
consume numbers.

## 8. Uploads (Phase 2)

- Allow-list: PDF, PNG, JPEG. Detect by magic bytes, not extension or
  client-supplied content type.
- Maximum 10 MB, enforced by DRF parser and storage validation.
- Randomised object keys; original filenames are never used as keys.
- Private bucket, no public ACLs. Downloads via pre-signed URLs valid for five
  minutes.
- `PENDING_SCAN → CLEAN | REJECTED`. Physicians cannot see pending/rejected
  files. ClamAV is a port with a Fake adapter for tests/local.
- Filenames/content are never logged.

## 9. Encryption and secrets

- TLS everywhere; HSTS in production.
- PostgreSQL and object storage use provider-managed encryption at rest. Buckets
  are private and versioned where available.
- Clinical free text and sensitive identifiers use `EncryptedTextField`
  (`MultiFernet`, AES-128-CBC + HMAC-SHA256). The first configured key encrypts;
  all keys decrypt, enabling rotation. Key material comes from the environment
  or a secrets manager, never the repo.
- `FIELD_ENCRYPTION_KEYS` is required in production and validated at startup.
- Secrets are injected via environment/secrets manager. `.env` is gitignored;
  `.env.example` documents every variable. Production fails fast if a required
  value is missing.
- No secrets are baked into the Docker image. Build-time dummy values are not
  present in the runtime environment.

## 10. Logging, audit and observability

- structlog emits JSON. Every request carries `X-Request-ID` and a structlog
  context variable; the id appears in logs and API error envelopes.
- Logs contain route, method, status, request id, user **id** (never email), and
  latency. Request bodies are never logged for auth or clinical endpoints.
- The redaction processor removes values for password/token/authorization/
  cookie/email/phone/name/address/clinical-note keys and scrubs email addresses,
  Bearer tokens and JWTs embedded in strings. Tests cover this.
- Sentry initialisation sets `send_default_pii=False`,
  `max_request_body_size="never"`, `include_local_variables=False`, and a
  `before_send` scrubber that removes bodies, cookies, auth headers, user
  identifiers, exception messages and logentry messages. If Sentry is disabled
  no data leaves the process.
- Celery task arguments contain UUIDs only. Email delivery errors are converted
  to a sanitised exception so recipient addresses do not appear in tracebacks.
- Phase 2 adds an append-only `AuditLog` for every clinical read and write.
  Audit diffs are redacted for encrypted fields.

## 11. Peru: Ley N.° 29733

**LEGAL_REVIEW:** this section records engineering intent, not legal advice.
Counsel must confirm retention periods, consent wording, the complaints-book
fields and cross-border transfer mechanisms.

Implemented/planned:

- **Consent records** for Terms of Service, Privacy Policy and health-data
  processing with document version, timestamp, IP and user agent. Versions come
  from the current `LegalDocument` rows; clients cannot choose an outdated
  version. Legal copy is seeded pending legal approval.
- **Purpose limitation:** data is used for care delivery, scheduling, billing and
  legally required records; marketing consent is separate and not assumed.
- **Access/export:** Phase 2 adds a data-subject export of profile, assessments,
  labs, evaluations, prescriptions, messages and consents (machine-readable).
- **Rectification:** profile correction endpoints plus a staff-reviewed request
  flow for clinical records.
- **Deletion/anonymisation:** a workflow that removes direct identifiers while
  preserving legally required financial/clinical records in de-identified form.
  Hard deletion is used where no retention obligation applies.
- **Retention:** configurable per data class; defaults must be approved by
  counsel. Backups follow the same retention and are purged on schedule.
- **Cross-border transfers:** infrastructure region and ESP/Sentry processing
  locations must be reviewed and documented before production launch.
- **Breach response:** runbook covers containment, impact assessment,
  notification decision and evidence preservation. **LEGAL_REVIEW:** statutory
  deadlines and authority notifications.

## 12. Supply chain and CI security

- `uv.lock` is committed; CI and Docker use `--frozen`.
- CI runs ruff, mypy, pytest (≥85% coverage), migration drift, OpenAPI
  validation, `check --deploy`, pip-audit, bandit, gitleaks and a Docker build.
- Pre-commit runs ruff, format, gitleaks, bandit and mypy.
- Dependabot groups minor/patch Python updates; security updates are reviewed
  promptly. Major upgrades require an ADR.
- The runtime image is multi-stage, non-root, and contains no compilers or build
  tooling.

## 13. Security review checklist for every phase

1. New endpoint has an explicit permission class and selector scoping.
2. Role matrix test updated.
3. Clinical reads/writes emit audit entries.
4. No PHI in logs, task args, error messages or Sentry.
5. List endpoints have `assertNumQueries` coverage.
6. OpenAPI has documented 400/401/403/404/409/429 where applicable.
7. `check --deploy` and schema validation remain green.
8. New dependency justified in an ADR and audited.
