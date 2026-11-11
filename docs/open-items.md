# Open items, assumptions and review flags

This file is the single source of truth for unfinished work. Anything marked
`TODO(...)` is deliberately incomplete, not a hidden stub.


## API Contract Part 1 open decisions

- `LEGAL_REVIEW`: replace the seeded legal-document sections (`CONTENT PENDING
  LEGAL REVIEW`) with approved Terms, Privacy Policy and health-data text. The
  current versions are `2026-11-01`.
- `LEGAL_REVIEW`: confirm the Complaints Book required fields, categories,
  document/phone validation and 24-hour response SLA.
- `LEGAL_REVIEW`: confirm the registration duplicate-email trade-off in
  `docs/contract-deviations.md`: the contract requires a generic 400 for existing
  addresses, which is observable.
- `LEGAL_REVIEW` (Figma 7.4): the Terms still mention the US 503A/503B pharmacy
  regime, an "approved service jurisdiction" and "verified ID". Replace with
  approved Peruvian regulatory wording and confirm whether ID verification is in
  scope.
- `LEGAL_REVIEW` (Figma 7.4): sign off the "100% money-back guarantee" wording;
  the corresponding refund flow is a Part 3 (orders) item.
- `LEGAL_REVIEW` (Figma 7.4): confirm whether the 24-hour Complaints Book
  response promise is an operational SLA and whether the anonymous status lookup
  (`GET /public/complaints/{number}/?email=...`) should stay.
- `[OPEN]`: choose the captcha provider. `TurnstileCaptchaVerifier` is available;
  production fails fast unless a real verifier and secret are configured.
- `[OPEN]`: confirm supported languages. `Accept-Language: en|es` and framework
  localization are wired; custom messages remain English pending copy approval.
- `[OPEN]`: confirm the real email domain. `adelgaceya.pe` is taken from the
  logo and is used in the v1.2 demo values.
- `[OPEN]`: real footer phone/address, social links, site legal entity and
  marketing statistics must be entered in the admin/database. The v1.2 demo
  contact values are seeded by `seed_demo` and must be replaced before launch.
- `[OPEN]`: testimonial consent evidence and the 4.92 rating claim need business
  review. Only `is_published` testimonials are returned.
- `[OPEN]` (Figma 7.3): the "Forgot password?" link, the signup consent checkbox
  and the success/error screens for contact and Complaints Book are not designed
  yet. The backend endpoints they call exist.
- `TODO(part-1)`: public `PUT/PATCH/POST` content management endpoints for legal
  documents arrive with Part 5; the model now supports versioning, `is_current`
  and immutable published rows, while the Part 1 admin is read-only.
- `TODO(part-1)`: complaint staff responses/status changes require a Part 5
  workflow; originals are already immutable in admin.
- `TODO(part-1)`: enforce `HasCurrentConsents` and `IsEmailVerified` on the
  patient clinical endpoints as they land in Part 2. Assessment create and
  submit now enforce Terms, Privacy and health-data consent; submit also
  requires a verified email. Other Part 2 writes remain scoped to the owner.
- `TODO(part-2)` (Figma 7.5): fix the patient Overview stepper, which skips step
  4 (3 -> 5).
- `TODO(part-2)` (Figma 7.5): correct the lbs/kg projection copy. 82 kg is about
  181 lbs and 70 kg about 154 lbs, so a 12 kg loss is about 26–27 lbs, not the
  current "200 lbs -> 158 lbs / -42 lbs".
- `TODO(part-2)` (Figma 7.5): replace the Male/Female/Other buttons in the "Date
  of birth" card on Body Measurements with a date input.

## API Contract Part 2 open decisions

- `[OPEN]`: confirm the evaluation fee. The patient screen and seeded default
  use `25.00`; the admin screen shows `$49.00`. The fee is program
  configuration (`Program.fee_amount`), snapshotted at submit.
- `[OPEN]`: confirm the fee currency. The contract shows USD and the business is
  in Peru; `Program.fee_currency` defaults to `USD`. Set it to `PEN` if that is
  the commercial decision. No conversion is performed.
- `[OPEN]`: confirm the payment methods. Only `CULQI` is exposed; the provider
  contract, currencies, webhook signature and refund rules are unconfirmed.
- `[OPEN]`: confirm whether more programs are launched. Only "Weight Loss" is
  seeded (`apps/assessments/migrations/0002_seed_weight_loss.py`); the model and
  API support more active programs with their own published questionnaire.
- `[OPEN]`: `Sex.OTHER` is accepted and stored but the clinical/BMI handling is
  undefined. BMI itself does not use sex; any future sex-specific rule needs a
  clinical decision.
- `[OPEN]`: the 30-day cooldown after `NOT_SUITABLE` is a program setting
  (`not_suitable_cooldown_days`), not a contract constant. Confirm the period and
  whether a physician override is needed.
- `[OPEN]`: the 24–48 hour review estimate is configurable copy
  (`estimated_review_hours_*`), not an SLA. Confirm the operational target and
  escalation path.
- `[OPEN]`: what "minimum necessary fields" staff may see on an evaluation. Part
  2 currently returns the same patient-facing evaluation shape to staff/admin.
- `[OPEN]`: whether physicians should see unassigned `SUBMITTED` evaluations.
  The selector currently exposes assigned evaluations plus the unassigned
  `SUBMITTED` queue; the Part 3 queue UI is not built.
- `[OPEN]`: the automated decline path. `NOT_ELIGIBLE` creates `SCREENED_OUT`;
  the patient can request a physician review once, but there is no automatic
  re-screen or staff triage workflow in Part 2.
- `[OPEN]`: refunds when a paid assessment is cancelled. Part 2 records the
  cancellation but never charges back; refunds belong to Part 3/4.
- Done in Part 3: the signed Culqi webhook (`POST /webhooks/culqi/`) finalises
  the fee and moves the evaluation to `SUBMITTED`.
- `TODO(part-3)`: replace `CulqiPaymentProvider` (currently a `503` placeholder)
  with the real adapter once the merchant contract is signed.
- `TODO(part-2-deploy)`: replace `NoopLabScanner` with a real ClamAV adapter.
  The no-op scanner marks files `CLEAN` and is local/test-only.
- `TODO(part-3)`: generate the prescription PDF. Part 2 can only serve a
  pre-signed link when the physician workflow has stored a `pdf_key`.
- `TODO(part-3)`: physician lab recommendations. The model stores the snapshot
  and `recommended_by`; Part 2 has no endpoint to set them.
- `[OPEN]`: confirm the projection evidence and disclaimer. The model is
  configurable (`projection_weekly_loss_kg`, months, copy) and is illustrative,
  not a clinical guarantee.
- `[OPEN]` (Figma 9): fix the Part 2 design defects recorded in
  `docs/figma-screens-part2.md` (date-of-birth control, stepper numbering,
  lbs/kg copy, "Culqi" spelling, lab filename, fee mismatch, duplicate
  medication question, Healthy/Normal label, duplicate screening options,
  admin question-editor wording, missing error states).
- `[MEDICAL_REVIEW]`: approve the seeded Weight Loss questionnaire, option
  wording, `min_bmi`, age limits and every screening effect. All are
  admin-editable; none is a hardcoded clinical rule.
- `[LEGAL_REVIEW]`: approve the lab-file retention period, deletion policy and
  who may download clinical files. Files are private and downloads are audited,
  but the retention schedule is not set.

## API Contract Part 3 open decisions

Every `[OPEN]` value below is configuration (settings or admin data), not a
code constant. See `docs/contract-deviations.md` items 46–74.

- `[OPEN]` (11.4.1): package prices, billing type, shipping fee and tax (IGV)
  display, and whether subscriptions are the main model. Seeded placeholder
  prices: Monthly Protocol S/ 599, Custom 3-Month Protocol S/ 1,599, Monthly
  Subscription S/ 599 per month. `ORDER_SHIPPING_FEE` (0.00) and
  `ORDER_TAX_RATE` (0) are settings; whether prices include IGV is undecided.
- `[OPEN]` (11.4.4): booking rules. `CONSULTATION_MIN_BOOKING_LEAD_HOURS` (12),
  `CONSULTATION_RESCHEDULE_MIN_HOURS` (24), `CONSULTATION_CANCEL_MIN_HOURS` (2),
  join window (10 min before, 15 min after), request expiry (2 h before the
  start), slot length per consultation type (30/45/20 min) and whether the
  initial visit must be with the reviewing physician (not enforced).
- `[OPEN]` (11.4.5): delivery coverage, carriers and cold chain. Only Lima
  Metropolitana and Callao districts are deliverable; edit
  `Ubigeo.is_deliverable` in the admin. Delivery estimate is 2 to 5 days
  (`ORDER_DELIVERY_DAYS_*`).
- `[OPEN]` (11.4.6): dose logging and adherence, push notifications and device
  tokens, message attachments. None is in v1; `next_dose` is computed from the
  schedule.
- `[OPEN]` (11.4.7): default language (`es`) and supported timezones (any IANA
  zone is accepted; default `America/Lima`).
- `[OPEN]`: physician search uses the CMP number (`license_label`), not the
  Figma's US "NPI".
- `[OPEN]`: renewal retry schedule `SUBSCRIPTION_RETRY_DAYS` (1, 3, 7) and what
  happens after the last failure (stays `PAST_DUE` for staff follow-up).
- `[OPEN]`: email for care-critical notifications (evaluation, labs, support
  reply) is always sent; confirm whether patients may opt out.
- `[OPEN]`: country is fixed to `PE`.
- `[VERIFY IN FIGMA]`: section-visibility rules, order filter tabs, message
  filter pills, and every missing screen listed in
  `docs/figma-screens-part3.md`.
- `[VERIFY WITH CULQI DOCS]`: webhook signature header and scheme
  (`apps/payments/culqi.py`), event names, card storage (customers/cards API)
  and refunds. `CulqiPaymentProvider` still returns `503`.
- `[VERIFY WITH PROVIDER DOCS]`: Daily webhook signature
  (`apps/payments/daily.py`), room and meeting-token API.
  `DailyVideoProvider` still returns `503`.
- `[LEGAL REVIEW]` (11.4.2): electronic invoicing (boleta/factura) and when the
  billing document is issued (`billing_document` stays `null` until an
  invoicing provider is connected); cancellation, pause and refund terms; the
  money-back guarantee; consultation recording (off,
  `VIDEO_RECORDING_ENABLED=False`); retention of messages and consultation
  data.
- `[MEDICAL REVIEW]` (11.4.3): product safety information
  (`Product.safety_information_markdown`), the messaging safety notice and
  emergency number (`SiteSettings.messaging_safety_notice`, seeded with SAMU
  106), who may be messaged, nurse access to clinical data, and the lifestyle
  essentials copy (`Program.lifestyle_essentials`).
- `[OPEN]` (Figma 11.2): fix the Part 3 design defects recorded in
  `docs/figma-screens-part3.md`.
- `TODO(part-4)`: physician endpoints that accept/decline/complete
  consultations, manage availability and create treatments. Service functions
  exist (`apps/consultations/services.py`: `physician_accept`,
  `physician_decline`, `physician_complete`).
- `TODO(part-5)`: staff endpoints for order status, tracking, refunds,
  packages, products, consultation types, nurse assignment and the audited
  support access to messages. Today these are admin-only.

## API Contract Part 4 open decisions

Every value below is configuration. See `docs/contract-deviations.md` items 75–98.

- `[OPEN]` (10.4.3): who sees the queue. Set `Program.allowed_specialties` in the
  admin; empty means every verified physician.
- `[OPEN]`: claim auto-release time (`EVALUATION_CLAIM_TIMEOUT_HOURS`, 4) and
  the review service-level target (not enforced).
- `[OPEN]`: whether a physician-initiated reschedule needs patient
  re-confirmation. Today the status is kept and the patient may cancel without
  the 2 h limit.
- `[OPEN]`: mandatory MFA roles (`MFA_REQUIRED_ROLES`, default
  `PHYSICIAN,STAFF,ADMIN`) and the invitation expiry
  (`PHYSICIAN_INVITATION_TTL_DAYS`, 7). The demo accounts must enrol in MFA
  after `seed_demo` in environments where it is enforced.
- `[OPEN]` (10.4.4): patient hand-off between physicians and coverage while a
  physician is away. Not supported; care relationships are derived from data.
- `[OPEN]` (10.4.5): patient check-ins ("30-day metabolic check-in"). No API.
- `[OPEN]`: `TREATMENT_UPDATED` notification type (named in 5.4, missing from
  the section 1 enum table) and the reuse of `CONSULTATION_REMINDER` for a
  physician reschedule.
- `[OPEN]`: the follow-up hint from a completed visit is stored but Part 3's
  overview has no field to show it.
- `[OPEN]`: My Patients `status` is `null` for patients with no approval or
  treatment (for example consultation only).
- `[MEDICAL REVIEW]` (10.4.1): product guardrails (`Product.allowed_doses`,
  `starting_doses`, `max_refills`; seeded placeholders for semaglutide), whether
  flagged answers need a written justification, the contraindication list, and
  nurse access to clinical data (nurses can message assigned patients only).
- `[LEGAL REVIEW]` (10.4.2): Peruvian e-prescription and digital-signature
  rules (the generated PDF is unsigned), attestation wording, the
  `CLINICIAN_AGREEMENT` text (seeded placeholder), clinical-note retention and
  the patient's right of access to notes, consent for recording video visits.
- `[OPEN]` (Figma 10.2/10.3): fix the defects and add the missing clinician
  screens listed in `docs/figma-screens-part4.md`.
- `TODO(part-5)`: staff endpoints to invite physicians, verify credentials
  (sends `CREDENTIALS_VERIFIED`/`REJECTED`), manage specialties, product
  guardrails and program specialty rules, review flagged subscriptions, and add
  `staff_type`. Until then these are admin actions; saving a new invitation in
  the admin shows the token once.
- `TODO(part-4-deploy)`: a real Daily adapter (owner tokens, end room) and a
  signing provider for prescriptions.

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

Completed with Part 2 (see `apps/assessments`):

- Resumable, versioned assessment with typed steps, server-side BMI and
  configurable eligibility rules, including unit tests.
- Lab upload pre-signed flow, magic-byte validation, 10 MB limit, randomized
  keys, scanner port and the scan-status gate.
- Append-only `AuditLog` with a `record_audit` helper; non-owner clinical reads
  and every clinical download are audited.
- `Idempotency-Key` on assessment create/submit and fee payment.
- RBAC gates for the Part 2 endpoints.

Still open in Phase 2 / Part 3:

- Done in Part 3: patient profile with unit preferences (`PatientProfile`) and
  clinician profiles (`ClinicianProfile`). Assessments still store their own
  display units.
- `TODO(phase-2)`: RBAC matrix test that enumerates every endpoint × role. Part
  2 has targeted role tests, not the full matrix.
- `TODO(phase-2)`: data export (access) and rectification request endpoints.
- `TODO(phase-2)`: retention/anonymisation tasks with legal-approved periods.
- `TODO(phase-2)`: query-count (`assertNumQueries`) tests on every list endpoint.
- `TODO(phase-2)`: the full clinical data model (profiles and physician
  licensing) is deferred; Part 2 uses the base `User` role.

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
- `TODO(part-1)`: the registration endpoint follows the contract: 201 on
  success, generic 400 on an existing email. The frontend must handle both and
  must not infer account existence from the error beyond what the contract
  already exposes.
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
