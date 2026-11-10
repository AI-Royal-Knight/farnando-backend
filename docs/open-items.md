# Open items, assumptions and review flags

This file is the single source of truth for unfinished work. Anything marked
`TODO(...)` is deliberately incomplete, not a hidden stub.

## Legal and clinical review

- `LEGAL_REVIEW`: approve the Terms, Privacy Policy and sensitive-health-data
  consent texts and their version identifiers (`CONSENT_*_VERSION`).
- `LEGAL_REVIEW`: retention periods per data class (clinical, billing, audit,
  messaging, support, backups) and the anonymisation rules for each.
- `LEGAL_REVIEW`: cross-border transfer mechanism/locations for AWS/S3, the
  email ESP and Sentry under Ley N.° 29733.
- `LEGAL_REVIEW`: exact Complaints Book ("Libro de Reclamaciones") fields,
  wording, numbering rules and response deadlines.
- `LEGAL_REVIEW`: national data-protection authority notifications and breach
  deadlines.
- `CLINICAL_REVIEW`: BMI/eligibility thresholds, exclusion criteria, pregnancy/
  breastfeeding rules, age limits and lab requirements. These are configuration,
  never hard-coded assumptions.
- `CLINICAL_REVIEW`: emergency messaging disclaimer and escalation policy.
- `LEGAL_REVIEW`: whether consultation recording is permitted and under what
  consent/retention conditions (default is off).

## External contracts and credentials

- `TODO(phase-4)`: confirm Culqi merchant contract, supported currencies,
  webhook signature scheme and refund rules; implement `CulqiPaymentProvider`.
- `TODO(phase-3)`: confirm Daily.co plan, data-processing terms and region;
  implement `DailyVideoProvider`.
- `TODO(phase-1-deploy)`: provide real Google OAuth client ID/secret and test
  the consent screen in staging.
- `TODO(phase-1-deploy)`: provide ESP credentials (Mailgun/SendGrid/Postmark)
  and verify sender-domain DNS (SPF/DKIM/DMARC).
- `TODO(phase-2)`: provide ClamAV endpoint/service and confirm malware
  signature update/monitoring ownership.
- `TODO(phase-1-deploy)`: choose the hosting target. The compose/CI image is
  generic; the deployment workflow is intentionally not vendor-specific yet.

## Phase 2

- `TODO(phase-2)`: patient/physician/staff profiles, unit preferences and
  demographic validation.
- `TODO(phase-2)`: RBAC matrix test that enumerates every endpoint × role.
- `TODO(phase-2)`: append-only `AuditLog` service/mixin covering every clinical
  read and write, with redacted diffs.
- `TODO(phase-2)`: versioned resumable assessment, typed steps, server-side BMI
  and configurable eligibility rules with unit tests.
- `TODO(phase-2)`: lab upload pre-signed flow, magic-byte validation, 10 MB
  limit, randomized keys, ClamAV hook and `PENDING_SCAN/CLEAN/REJECTED` gate.
- `TODO(phase-2)`: idempotency service + `Idempotency-Key` on assessment submit.
- `TODO(phase-2)`: data export (access) and rectification request endpoints.
- `TODO(phase-2)`: retention/anonymisation tasks with legal-approved periods.
- `TODO(phase-2)`: query-count (`assertNumQueries`) tests on every list endpoint.

## Phase 3

- `TODO(phase-3)`: evaluation state machine service, status history, physician
  queue, approve/reject/request-labs decisions, SLA reminders.
- `TODO(phase-3)`: consultation scheduling, availability, conflict checks,
  video adapter integration and per-consultation tokens.
- `TODO(phase-3)`: prescription creation with dose/frequency/next dose/refills
  and atomic refill decrement.

## Phase 4

- `TODO(phase-4)`: catalog, content management, programs/packages, pricing with
  server-side recalculation.
- `TODO(phase-4)`: orders, subscriptions, shipments/tracking, renewals, dunning.
- `TODO(phase-4)`: payments, signed webhooks, raw-event storage, unique event
  ids, out-of-order handling, refunds.
- `TODO(phase-4)`: order/payment idempotency and optimistic concurrency.
- `TODO(phase-4)`: reports cache invalidation hooks for revenue/MRR/orders.

## Phase 5

- `TODO(phase-5)`: messaging threads/messages/attachments/read receipts with
  cursor pagination, `since` polling and ETags; Channels-ready schema.
- `TODO(phase-5)`: support tickets, contact messages, complaints book with
  immutable sequential numbering.
- `TODO(phase-5)`: notification preferences and in-app notifications.

## Phase 6

- `TODO(phase-6)`: cached analytics endpoints (revenue, MRR, orders, users,
  registered/active, evaluation throughput/outcomes, churn) with 5-minute TTL
  and event-driven invalidation.
- `TODO(phase-6)`: N+1/performance review, load-test notes, DB index review.
- `TODO(phase-6)`: optional OpenTelemetry/Prometheus setup and dashboards.
- `TODO(phase-6)`: full seed_demo covering every screen (assessment, labs,
  evaluation, prescription, order, messages) for Figma-accurate demos.
- `TODO(phase-6)`: final security review against ASVS, backup restore drill and
  incident tabletop.

## Phase 1 known limitations / follow-ups

- `TODO(phase-1)`: `pip-audit` findings, if any, must be triaged; the CI gate is
  enabled but advisory data changes over time.
- `TODO(phase-1)`: the compose MinIO image is the Bitnami legacy build (pinned by
  digest) because the official MinIO images are unavailable in some registry
  mirrors. Production uses managed S3-compatible storage; revisit only if the
  local dev image becomes unavailable.
- `TODO(phase-1-deploy)`: choose the hosting target; `deploy/README.md` contains
  a sample Render blueprint. The deploy workflow builds/pushes the image and
  triggers a Render deploy hook when configured; adapt migrations/release
  steps for the final target.
- `TODO(phase-1)`: OpenAPI examples cover auth; assessment submit, evaluation
  decisions and order creation examples arrive with those endpoints.
- `TODO(phase-1)`: docs schema is staff-only and relies on an authenticated
  admin session for Swagger UI in production. If the team wants JWT-only docs,
  add a short-lived docs token flow.
- `TODO(phase-1)`: access tokens cannot be revoked before their 15-minute
  expiry; sensitive future operations must require re-authentication.
- `TODO(phase-1)`: the registration endpoint deliberately returns 202 for
  existing emails; the frontend must present "check your email" rather than a
  success-with-tokens flow.
- `TODO(phase-1)`: no SMS provider yet; phone verification is deferred to
  Phase 2/5 if the product requires it.

## Assumptions

- PostgreSQL 16 is available in every environment; tests do not run on SQLite.
- Redis is the cache, Celery broker and result backend.
- S3-compatible private storage is configured through env; MinIO is dev-only.
- `America/Lima` is the business timezone; all storage is UTC.
- API clients treat UUIDs as opaque.
- The SPA is a separate origin and uses bearer JWTs (ADR-0002).
- The docs UI is an internal staff tool, not a public developer portal.
