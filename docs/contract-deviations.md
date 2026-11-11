# Contract deviations and interpretation notes — Parts 1–4

Status: implementation notes for owner review. The contract is treated as the
source of truth. Where the contract is internally ambiguous, the literal
behaviour is implemented unless noted. No endpoint, field or enum is renamed.
Part 1 is v1.2, and Parts 2, 3 and 4 are v1.0. v1.2 adds the Figma review record (section
7), now mirrored in `docs/figma-screens-part1.md`, and fixes the demo contact
values in section 3.1. Part 2 notes continue from section 21.

1. **Registration duplicate-email conflict (contract contradiction).**
   The contract requires `201` on successful registration and `400
   validation_error` when the email already exists, while also saying the
   endpoint must not confirm which emails exist. A `201` vs `400` difference is
   observable, so the two requirements cannot both hold. Implemented literally:
   success is `201`; an existing email returns `400 validation_error` with the
   generic message "Unable to create an account with this email." and an
   account-exists email is sent. This is a privacy trade-off requiring owner
   confirmation; alternative is a uniform `202` for every address.

2. **`consent_required` status.** The global table lists it as `403`, while
   registration documents it as `400 consent_required`. Implemented contextually:
   `403 consent_required` for authenticated protected endpoints, `400
   consent_required` for registration/Google signup input validation.

3. **Legal document content is not supplied.** The contract specifies structure
   and headings but not the legal text. Initial `LegalDocument` rows are seeded
   with the correct versions and headings but empty/placeholder sections marked
   `LEGAL_REVIEW`; they must be replaced before launch. Registration validates
   against the DB versions.

4. **`full_name`.** The existing schema used `first_name`/`last_name`. Part 1
   exposes only `full_name` (and registration accepts only `full_name`). A
   `full_name` field is populated by migration; the legacy internal columns are
   retained for compatibility but are not exposed by the API.

5. **`UserSummary.assessment_status`.** Part 2 owns assessments. Until that
   model exists, the field is always `null` for patients and non-patients. This
   satisfies the declared type without inventing statuses.

6. **Document/complaint optional fields.** `document_type`, `document_number`
   and `phone` are `[OPEN]` in the contract. They are accepted, optional, and
   validated when present (DNI 8 digits, CE 9–12 alphanumeric, passport 6–20,
   RUC 11 digits, phone E.164). They are stored for later legal review.

7. **Complaint status lookup (5.2).** Marked `[OPEN]` in the contract. Because
   the instruction is to implement the contract exactly and the endpoint is
   specified, it is included.

8. **Captcha provider.** `[OPEN]`. Implemented as a configurable port. Local and
   test use a no-op verifier. Production requires a verifier and secret to be
   configured; the provider-specific adapter is selected by env. Until a
   provider is chosen, production public forms will reject misconfiguration
   rather than silently accept unverified submissions.

9. **Languages.** `[OPEN]`. `Accept-Language` and `LocaleMiddleware` are wired
   for `en`/`es`; the error envelope `message` has Spanish mappings for all
   contract codes and framework/DRF field details localize. Copy for ordinary
   product text still requires approval.

10. **Idempotency for anonymous complaints.** The required key is scoped to the
    endpoint, key and a hash of the request IP so one anonymous caller cannot
    read another caller's replay. Same key + different body returns
    `409 idempotency_conflict`.

11. **Testimonial consent.** Only `is_published=True` rows are returned, as
    specified. A `patient_consent_recorded` flag is stored and surfaced to
    admins; enforcing it before publish is a Part 5 admin workflow.

12. **Complaint SLA.** `response_due_at` uses the configurable
    `COMPLAINT_RESPONSE_SLA_HOURS` setting, default 24 hours per the Figma copy.

13. **Token error distinction.** Access/refresh tokens map to `token_invalid` or
    `token_expired`; email/reset tokens map to `token_invalid`/`token_expired`
    as `400` errors. Refresh rotation failures always use `token_invalid` per
    the contract.

14. **Timestamps.** DRF is configured to render UTC timestamps with a trailing
    `Z`; dates use `YYYY-MM-DD`.

15. **Unknown fields.** A strict serializer base rejects unknown write fields
    with `400 unknown_field`, including nested consent items. The verification
    resend endpoint (1.7) takes no body and uses the same strict check, so an
    unexpected body field is rejected rather than silently ignored.

16. **Change-password wrong-current-password status (1.11).** The contract lists
    both `401 invalid_credentials` and `400` for a wrong current password, then
    explicitly says "**use 400**". Implemented as `400 validation_error` with a
    `current_password` field error so the client does not log the user out.

17. **Demo contact values (3.1).** The v1.2 demo values are written to the
    database by `seed_demo` (`contact@adelgaceya.pe`, `+51 1 555 0100`,
    `Av. Larco 1234, Miraflores, Lima 15074, Perú`, `Adelgaceya`). No contact
    value is hardcoded in API code; a non-demo database starts empty until an
    admin fills it in. The real values still need owner confirmation.

18. **Social-link platforms (3.1).** The contract's `platform` list ends with
    `...`; the stored value is therefore an admin-editable string, while the
    seeded values use `FACEBOOK` and `INSTAGRAM`. See `docs/open-items.md`.

19. **django-axes lockout response (1.2).** axes' default lockout page is plain
    text. A small callable (`apps.accounts.lockout.axes_lockout_response`)
    renders the standard error envelope with `429 account_locked`, a localized
    message, the request id and `Retry-After` (15 minutes), matching the shared
    error contract.

20. **Figma review (section 7).** Sections 7.1–7.3 are design/copy changes, and
    7.5 lists frontend defects found for Part 2. They do not add or change API
    surface in Part 1; they are recorded in `docs/figma-screens-part1.md` and
    tracked in `docs/open-items.md`.

## Part 2 (v1.0) — assessments, fees, labs and evaluation

Status: implementation notes for owner review. Part 2 is implemented in
`apps/assessments`. The contract is still the source of truth; the notes below
record only places where it is silent, ambiguous or internally inconsistent.
Admin-only screening configuration is never exposed by a patient endpoint.

21. **Program availability.** `GET /programs/` returns only active programs.
    `is_available` is `true` only when the program has a published
    questionnaire. The field is computed, not stored: an active program without
    a published questionnaire can be listed (so the UI can explain it) but
    cannot be started. Starting an inactive/unavailable/unknown program is 404.

22. **Assessment create, resume and idempotency.** `POST /assessments/` requires
    `Idempotency-Key` and returns `201` for a new draft, `200` when the patient
    already has a draft for that program (resume), and the stored response when
    the same key and body are replayed. A different key with the same body still
    resumes the same draft. A partial unique constraint enforces one draft per
    patient and program. Creation returns `409 conflict` while an active
    evaluation exists or during the `NOT_SUITABLE` cooldown. The list endpoint
    supports `?status=` and uses the global page-number envelope.

23. **Canonical units and display preference.** Weights are stored in kg (2 dp)
    and heights in cm (1 dp); lb/in are display-only and converted with exact
    constants (`0.45359237`, `2.54`). Part 3 owns the patient profile and its
    unit preference, so Part 2 stores the units of the last measurements request
    on the assessment and uses them for detail/summary responses. Responses
    always include the canonical value plus `display: {value, unit}`; no client
    value is trusted for BMI or screening.

24. **BMI, target BMI and category.** BMI is `kg / m²`, rounded half-up to one
    decimal. Categories are `UNDERWEIGHT < 18.5`, `HEALTHY 18.5–24.9`,
    `OVERWEIGHT 25–29.9`, `OBESE ≥ 30`. The contract requires a target below the
    current weight; this build also rejects a target BMI below `18.5` with
    `400 target_below_healthy_range`. That extra safety bound is a proposed
    clinical rule, not a contract constant.

25. **Measurement bounds and age.** Extra server bounds are 30–350 kg for
    current/target weight and 120–230 cm for height, plus a date of birth that
    cannot be in the future. Age below `Program.age_min` (default 18) returns
    `400 under_age`. Age above `Program.age_max` (default 75) returns
    `400 validation_error` on `date_of_birth` because the contract defines no
    `over_age` code. Both age limits are admin-editable and marked
    `[MEDICAL REVIEW]`.

26. **Screening.** The result is computed from admin-only effects on the chosen
    questionnaire options/questions plus `Program.min_bmi`. Precedence is
    `NOT_ELIGIBLE` > `NEEDS_REVIEW` > `ELIGIBLE`. Effects are editable in the
    admin and are never serialized. Before submission a complete assessment
    without a stored result is summarised as `ELIGIBLE`; the value becomes
    durable at submit. The seeded questionnaire and effects are proposals for
    medical review.

27. **Screening result and evaluation state.** `NOT_ELIGIBLE` creates an
    evaluation in `SCREENED_OUT` with fee `NOT_REQUIRED` and no payment path.
    `NEEDS_REVIEW` proceeds to payment and physician review, not to an automatic
    decline. A waived or zero fee skips `AWAITING_PAYMENT` and starts at
    `SUBMITTED` (`WAIVED` when explicitly waived, `NOT_REQUIRED` for a zero
    price). The submit response returns `next` as `PAY_FEE`,
    `SCREENED_OUT_MESSAGE` or `RECOMMENDED_LABS`.

28. **Questionnaire pinning and hidden answers.** An assessment pins the
    published questionnaire version at creation, so later questionnaire edits do
    not change an in-flight assessment. `show_if` supports equality against an
    earlier answer in the same flow. Answers to hidden questions are discarded
    (not stored) and omitted from step responses; exclusive options cannot be
    combined; only visible required questions count for completeness.

29. **Measurements are not a questionnaire step write.** The contract lists a
    measurements step and a step-upsert endpoint. The measurements step is saved
    through `PUT /assessments/{id}/measurements/` only; calling the step endpoint
    for that step returns `400 validation_error`. This keeps BMI and target
    validation in one service instead of duplicating it.

30. **Fee snapshot and amount mismatch.** The fee (amount, currency, title,
    inclusions, payment methods) is snapshotted from the program at submit and
    does not change retroactively. The seeded default is `25.00 USD`, matching
    the patient fee screen; the admin screen's `$49.00` example remains `[OPEN]`.
    `payment_methods` is `[{"code": "CULQI", "label": "Culqi"}]`.

31. **Payment finalisation.** `POST /assessments/{id}/fee/pay/` returns `200`
    with the payment attempt. A synchronous provider success is deliberately
    downgraded to `PENDING`: only the signature-verified provider webhook
    (Part 3) calls `PaymentService.mark_succeeded`, which marks the fee `PAID`
    and moves `AWAITING_PAYMENT → SUBMITTED`. A decline returns
    `402 payment_failed` and stores a `FAILED` payment. `CulqiPaymentProvider`
    is a placeholder that returns `503` until the commercial contract exists.
    Part 2 has no webhook route.

32. **Payment idempotency.** `fee/pay` requires `Idempotency-Key`, scoped to the
    caller + assessment, hashed over `payment_method` and `provider_token`.
    Replaying the key and body returns the stored attempt; reusing it with a
    different body returns `409 idempotency_conflict`. Payment attempts are
    append-only history; the fee endpoint returns the latest attempt.

33. **Lab upload and local-storage fallback.** The flow is pre-signed `PUT`
    (15-minute TTL), then `confirm`. Limits are 5 active uploads per assessment,
    10 MB, and PDF/PNG/JPEG; the client `content_type` must be in the allowed
    set and the server re-sniffs the magic bytes on confirm. With S3 the helper
    returns a real pre-signed PUT. With `FileSystemStorage` (tests/local only)
    it can only return a media URL, which is not a real pre-signed upload; this
    is a development fallback, not production behaviour.

34. **Antivirus.** Confirming queues a Celery scan and the upload stays
    `SCANNING` until the scanner returns `CLEAN` or `REJECTED`. The default
    `NoopLabScanner` marks files clean and must be replaced by a real ClamAV
    adapter in production. Downloads require `CLEAN`; every lab download is
    audited.

35. **Lab submit/skip.** `labs/skip` is allowed only from `NOT_STARTED` and sets
    `DEFERRED`; repeating it is `409 invalid_state_transition`. `labs/submit`
    requires at least one non-deleted `SCANNING`/`CLEAN` upload, otherwise
    `409 no_lab_files`; it sets labs to `SUBMITTED` and moves only
    `PENDING_LABS → SUBMITTED`. It does not skip payment or physician review.
    A `SCANNING` file may be submitted, but it remains invisible to physicians
    until the scan is `CLEAN`.

36. **Cancellation.** A draft can always be cancelled. After submission, only
    `AWAITING_PAYMENT`, `SUBMITTED` and `PENDING_LABS` may be cancelled.
    `SCREENED_OUT` is intentionally excluded: the contract exposes
    `request-review` for that state rather than cancellation. Other states
    return `409 invalid_state_transition`. Paid cancellations do not trigger an
    automatic refund (the refund flow is Part 3/4). The reason is encrypted at
    rest.

37. **Request review.** Allowed only while `SCREENED_OUT`, and only once per
    evaluation. The response keeps `evaluation_status: SCREENED_OUT` because the
    enum has no "review requested" status; `review_requested: true` and the
    stored `EvaluationReviewRequest` are the signal. A second request is
    `409 conflict`. The note is encrypted; there is no automatic re-review
    workflow in Part 2.

38. **Staff/physician read scope and minimum fields.** Patients read their own
    assessment/evaluation. Physicians read assigned evaluations plus unassigned
    `SUBMITTED` ones. Staff and admin can list/read assessments and evaluations
    across patients. The contract's "minimum necessary fields" for staff is
    `[OPEN]`; this build returns the same patient-facing detail, summary,
    questionnaire and evaluation shapes to staff/admin. That needs owner
    confirmation before it is exposed more widely. A draft assessment has no
    evaluation (`404`). Non-owner reads are audited. Staff cannot download lab
    files; only the owner, the assigned physician and admin can.

39. **Prescription download.** Requires evaluation `APPROVED`, an existing
    `Prescription`, a non-empty `pdf_key` and `can_download`. Otherwise the
    endpoint is `409 invalid_state_transition`. Part 2 only issues a short-lived
    pre-signed link; PDF generation belongs to the Part 3/4 physician workflow.
    Every download is audited, including the patient's own.

40. **Audit scope.** Non-owner reads of assessment detail, questionnaire,
    summary, fee, labs and evaluation are audited. Every lab and prescription
    download is audited even for the owner, because it is PHI egress. Owner
    reads are not audited to keep the trail meaningful. Audit metadata excludes
    clinical values; the audit row stores actor, action, object, request id, IP
    and user agent.

41. **Lab recommendations.** The patient sees the physician snapshot when one
    exists, otherwise the program's configured panels. `recommended_by` is
    `SYSTEM` or `PHYSICIAN`. Part 2 has no endpoint to set physician
    recommendations or `recommended_by`; that is part of the Part 3 physician
    workflow. Panels are admin-editable JSON.

42. **Projection.** Projection uses `4.345` weeks/month, the configured weekly
    loss, and clamps at the target weight. The estimated timeline is
    `ceil(goal / weekly_loss)` with a minimum of one week. The disclaimer and
    messages are configuration. This is illustrative copy, not a clinical
    guarantee.

43. **Part 2 error codes.** Added `payment_failed` (402), `assessment_locked`
    (409), `assessment_incomplete` (409, with `missing_steps`),
    `upload_limit_reached` (409), `no_lab_files` (409), `target_weight_invalid`
    (400), `target_below_healthy_range` (400) and `under_age` (400). Existing
    codes (`validation_error`, `conflict`, `invalid_state_transition`,
    `idempotency_*`, `file_too_large`) keep their Part 1 meaning.

44. **`assessment_status` on the user summary.** It now returns the status of
    the patient's latest assessment (`DRAFT`, `SUBMITTED`, `CANCELLED`) or
    `null` when none exists; non-patients keep `null`. This replaces the Part 1
    placeholder without changing the field type.

45. **Figma review (Part 2, section 9).** The section 9 design defects are
    frontend/copy issues and do not change the backend surface. They are
    recorded in `docs/figma-screens-part2.md` and tracked in
    `docs/open-items.md`.

## Part 3 (v1.0) — patient portal, consultations, treatments, orders, messaging

These notes were written before the Part 3 code, as the contract requires, and
updated while implementing. Nothing below renames an endpoint, field or enum.

46. **Nurses and `Role`.** Part 1's `Role` enum has no `NURSE`, and Part 3 must
    not add one. A nurse is a `STAFF` user with a `ClinicianProfile` whose
    `care_role` is `NURSE`; a physician is a `PHYSICIAN` user with
    `care_role=PHYSICIAN`. `MessageSenderRole` and the care-team `role` come from
    the clinician profile. The patient's nurse is `PatientProfile.assigned_nurse`
    (set by staff in the admin until Part 5 adds an endpoint).

47. **Data Part 4/5 will manage.** Part 3 reads physician headlines, availability
    rules, time off, consultation types, products, packages and treatments that
    Parts 4 and 5 will edit through the API. The models are created now and are
    editable in the Django admin; seed data covers the Figma examples.
    `ConsultationType` is seeded with `INITIAL` (30 min), `METABOLIC_RESET`
    (45 min) and `FOLLOW_UP` (20 min). The durations are `[OPEN]`.

48. **`Payment` is shared.** The Part 2 `Payment` table gains `purpose`,
    `refunded_amount`, `user`, `order` and `subscription`; `evaluation` and `fee`
    become nullable because order and renewal payments have neither. Existing
    rows are back-filled as `EVALUATION_FEE`. `PaymentStatus` gains
    `PARTIALLY_REFUNDED` as Part 3 section 1 defines. The Part 2 fee endpoints
    now also return `purpose` and `refunded_amount` (the Part 3 `Payment`
    shape).

49. **Section visibility.** Implemented as written in section 0. `MESSAGES` is
    enabled when the care team is not empty or a thread exists. The care team is
    the physician on any of the patient's evaluations, the prescribing physician
    of any treatment and the assigned nurse.

50. **`PatientProfile`.** Created lazily on first read. `created_at` is the
    user's `date_joined`, so it does not depend on when the profile row was
    first read. `date_of_birth` and `sex` come from the latest assessment that
    has measurements. In `PATCH`, any read-only field (`date_of_birth`, `sex`,
    `email`, `user.email`, `user.id`, `user.role`, `default_address_id`,
    `created_at`) returns `400 read_only_field`; unknown fields still return
    `400 unknown_field`. Turning on `email_marketing` stores the time of the
    opt-in as consent evidence.

51. **Address limits and errors.** The 11th address returns
    `409 conflict` (the contract gives the limit but no code). An unknown or
    non-district `ubigeo` is `400 validation_error`. When the default address is
    deleted, the newest remaining address becomes the default.

52. **Ubigeo codes and data.** Departments use 2-digit codes, provinces 4-digit
    and districts 6-digit INEI codes, so `parent` is always a prefix of the
    child. The migration seeds all 25 departments, the provinces of Lima, Callao
    and Arequipa, and the districts of Lima Metropolitana, Callao and Arequipa
    city. `python manage.py load_ubigeo <csv>` loads the full INEI table.
    `is_deliverable` is `null` for departments and provinces. Only the Lima
    Metropolitana and Callao districts are deliverable by default (`[OPEN]`
    coverage).

53. **Physician availability.** Weekly rules (weekday, start, end, in the
    physician's timezone) minus blocks (lunch, time off) minus open
    consultations (`REQUESTED`, `CONFIRMED`, `IN_PROGRESS`) minus slots that
    start less than `CONSULTATION_MIN_BOOKING_LEAD_HOURS` (12) from now. A slot's
    length is the consultation type's `duration_minutes`. `next_available_at` in
    the physician list uses the initial consultation type and looks ahead
    `available_within_days` days, or 30 days when that filter is absent.
    Availability responses send `Cache-Control: no-store`, so nothing is cached
    for more than 30 seconds.

54. **Booking concurrency.** Booking and rescheduling lock the physician's
    clinician profile row, recompute the slots and check that the requested
    start is one of them. A partial unique constraint on
    `(physician, start)` for open consultations is the database backstop. A lost
    race returns `409 slot_unavailable`, and so does a `start` that is not an
    available slot. Overlapping slots of different lengths are prevented by the
    lock and recompute step, not by the constraint.

55. **Consultation eligibility.** "Fee paid or waived" means an evaluation in
    `SUBMITTED`, `IN_REVIEW`, `PENDING_LABS` or `APPROVED` (as section 0 says),
    which also covers programs with no fee. Eligibility is checked before
    consent, so a patient with neither gets `403 evaluation_required`. An
    inactive physician, a physician not accepting consultations or an inactive
    type is `400 validation_error`. An `assessment_id` that is not the
    patient's is `400 validation_error`.

56. **Consultation timing settings.** `[OPEN]` values are settings read from
    the environment: lead time 12 h, reschedule 24 h, cancel 2 h, join opens
    10 min before the start and closes 15 min after the end, `REQUESTED` expires
    2 h before the start. Scheduled jobs (Celery beat, every 5 minutes) expire
    requests, mark `NO_SHOW` and send the 24 h and 1 h reminders.

57. **Video.** `DAILY` is behind a provider port. The default `FakeVideoProvider`
    (local and tests) returns a deterministic room URL and an opaque token. The
    real Daily adapter fails with `503` until credentials are set. Recording is
    off (`VIDEO_RECORDING_ENABLED=False`, `[LEGAL REVIEW]`). `leave` is allowed
    only for `CONFIRMED` and `IN_PROGRESS` (`409 invalid_state_transition`
    otherwise). The `meeting.ended` webhook sets `ended_at` but does not complete
    the visit; only the physician completes it (Part 4).

58. **Treatment `next_dose`.** There is no dose logging in v1 (`[OPEN]`), so the
    next due time is computed from the schedule in the patient's timezone:
    `DAILY` is today or tomorrow, `WEEKLY` is the next `day_of_week` (or the
    start weekday), `EVERY_TWO_WEEKS` and `MONTHLY` step from `started_at`, and
    `AS_NEEDED` is `null`. Times of day map to 08:00 (morning), 14:00
    (afternoon), 20:00 (evening) and 09:00 (any). Paused, completed and
    discontinued treatments have no next dose. The `status` filter accepts a
    comma-separated list; the default is `ACTIVE,PAUSED`.

59. **Treatment content.** `description`, `image_url` and
    `safety_information_markdown` (`[MEDICAL REVIEW]`) come from the product.
    `lifestyle_essentials` comes from the program. Both are admin-editable. The
    prescription download uses the treatment's PDF, falling back to the Part 2
    prescription PDF; with neither it returns `409 invalid_state_transition`,
    as in Part 2.

60. **Renewal requests.** `status` is always `REQUESTED` in Part 3. A request is
    pending until Part 4 sets `resolved_at`. Only `ACTIVE` treatments accept a
    request (`409 invalid_state_transition` otherwise).

61. **Package pricing.** Each package item has a unit price. The package `price`
    is the sum of `unit_price × quantity`, so the order lines always add up to
    the subtotal. Package currency defaults to `PEN`. Shipping
    (`ORDER_SHIPPING_FEE`, default 0.00), tax rate (`ORDER_TAX_RATE`, default 0,
    applied to subtotal plus shipping) and delivery days (2 to 5) are settings
    and `[OPEN]` (including whether prices already include IGV).

62. **Checkout validation codes.** An unknown or foreign `treatment_id`,
    `package_id`, `shipping_address_id` or `payment_method_id`, a non-`ACTIVE`
    treatment, an expired prescription, or a package from another program
    returns `400 validation_error` on that field. A `RECURRING` package needs a
    card that can be charged again: `SAVED_METHOD`, or `CULQI` with
    `save_payment_method: true`, otherwise `400 validation_error`. A `BOLETA`
    needs an 8 to 12 character document number; a `FACTURA` needs an 11-digit
    RUC and a `business_name` (`[LEGAL REVIEW]`). Billing details are encrypted
    at rest.

63. **Synchronous declines.** As in Part 2, a provider's synchronous success is
    stored as `PENDING` until the webhook arrives. A synchronous decline returns
    `402 payment_failed` and rolls back the whole request, so no order is left
    behind and the same `Idempotency-Key` can be retried. Webhook-reported
    failures move the order to `PAYMENT_FAILED` for `POST /orders/{id}/pay/`.
    Retrying while the latest attempt is still `PENDING` returns `409 conflict`
    to prevent a double charge.

64. **Order groups and cancellation.** `ACTIVE` is every status except
    `DELIVERED`, `CANCELLED` and `REFUNDED` (so a failed payment that needs
    attention is active). Cancelling a paid order asks the provider for a
    refund and restores the refill. The order stays `CANCELLED` when the refund
    webhook arrives because the state machine has no `CANCELLED` to `REFUNDED`
    transition; only the payment becomes `REFUNDED`.

65. **Order numbers.** `ADL-ORD-` plus a zero-padded sequence (`ADL-ORD-0001`)
    from a locked counter row, as for complaint numbers.

66. **Subscriptions.** Cancelling while the paid period is still running sets
    `cancel_at_period_end` and clears `next_billing_at`. A scheduled job sets
    `CANCELLED` at the period end. If no paid period is left (for example
    `PAST_DUE`), the subscription is cancelled at once. Cancelling twice returns
    `409 invalid_state_transition`. A pause with `resume_on` resumes
    automatically on that date. Renewal retries are 1, 3 and 7 days after the
    failure (`SUBSCRIPTION_RETRY_DAYS`, `[OPEN]`). After the last failure the
    subscription stays `PAST_DUE` for staff follow-up (Part 5). A successful
    renewal creates the next order as `PRESCRIPTION_VERIFIED` and consumes a
    refill. If no refill is left, the order is put `ON_HOLD` for a renewal
    request.

67. **Saved cards.** A rejected card token returns `400 validation_error`. A
    card can be deleted unless it is the only saved card and an open
    subscription (`ACTIVE`, `PAUSED`, `PAST_DUE`) uses it
    (`409 payment_method_in_use`). If other cards exist, those subscriptions
    move to the default (or newest) remaining card. The first card is the
    default.

68. **Webhooks.** `POST /webhooks/culqi/` and `POST /webhooks/video/daily/` check
    an HMAC-SHA256 signature over the raw body with a shared secret
    (`CULQI_WEBHOOK_SECRET`, `DAILY_WEBHOOK_SECRET`). The exact header and
    signing scheme are `[VERIFY WITH CULQI DOCS]` and
    `[VERIFY WITH PROVIDER DOCS]` and live only in the adapter modules. With no
    secret configured every request is rejected with `401`. Bodies over 256 KB
    return `413 payload_too_large` (a new code; the contract names the limit but
    not the code). Events are stored first, then processed by a Celery task;
    processing errors are saved on the event and never change the `200`.
    Webhooks are exempt from the anonymous throttle.

69. **Admin access (section 10).** `ADMIN` can read consultations, treatments,
    orders, payments and message threads across patients through the same
    `GET` endpoints, and every such read is audited. Writes stay patient-only.
    `PHYSICIAN` and `STAFF` get `403` on these endpoints because their views
    live in Parts 4 and 5.

70. **Messages.** Message bodies are encrypted at rest, so `search` on message
    text is done in Python over the patient's own threads (a bounded set). The
    list is cursor-paginated by `created_at`. `after` returns only messages newer
    than the given message. The `ETag` is a hash of the thread's latest message,
    message count, latest read time and the query string. Clinicians receive
    `NEW_MESSAGE` in-app only. Patients also get an email when
    `email_messages` is on. The email never contains the text.

71. **Notifications and preferences.** In-app notifications are always
    created. Emails follow the preferences: `email_messages` for `NEW_MESSAGE`;
    `email_orders` for `ORDER_STATUS_CHANGED` and the subscription types;
    `email_reminders` for consultation, dose and refill types. Evaluation,
    lab-request and support-reply emails are always sent because they are
    care-critical (`[OPEN]`). Part 2 evaluation transitions now emit
    `EVALUATION_STATUS_CHANGED` (or `LABS_REQUESTED` for `PENDING_LABS`). Titles
    and bodies are generic and never contain clinical data.

72. **New error codes.** `read_only_field` (400), `unsupported_delivery_area`
    (400), `evaluation_required` (403), `not_in_care_team` (403),
    `address_in_use` (409), `slot_unavailable` (409), `too_late_to_change`
    (409), `join_window_closed` (409, with `details.join_opens_at`),
    `no_refills_remaining` (409), `cancel_not_allowed` (409),
    `payment_method_in_use` (409) and `payload_too_large` (413).

73. **Overview.** The "most relevant" assessment is the newest one that is not
    cancelled, or the newest one when all are cancelled. `primary_action`
    follows the first-match rules literally, so a submitted evaluation whose labs
    are `NOT_STARTED` shows `UPLOAD_LABS` before `SET_APPOINTMENT`. `target_id`
    is the assessment for `PAY_FEE`, `RESUME_ASSESSMENT` and `UPLOAD_LABS`, the
    treatment (or the assessment if no treatment exists yet) for
    `ORDER_MEDICINE` and `VIEW_TREATMENT`, and `null` for `SET_APPOINTMENT` and
    `NONE`. "No consultation booked" means no `REQUESTED`, `CONFIRMED` or
    `IN_PROGRESS` consultation. `health_summary.subheadline` is program copy.

74. **Figma review (Part 3, section 11).** The design defects and missing
    screens do not change the backend surface. They are recorded in
    `docs/figma-screens-part3.md` and tracked in `docs/open-items.md`.

## Part 4 (v1.0) — clinician side

Written before the Part 4 code and updated while implementing. Amendments A1 to
A7 are applied.

75. **`staff_type` (A7).** Part 5 will add `staff_type` to staff accounts. Until
    then the Part 3 `ClinicianProfile.care_role = NURSE` on a `STAFF` user is the
    source of truth, and a future `staff_type` must be kept in sync with it.

76. **Physician profile storage.** `PhysicianProfile` is served from the Part 3
    `ClinicianProfile`, extended with `verification_status`,
    `verification_note`, `bio`, `specialty`, `license_number` (unique),
    `specialty_registration_number`, `phone` and `languages`. `full_name` is the
    display name ("Dr. Sofia Morales, MD"). `accepting_new_consultations` is the
    Part 3 `accepts_new_consultations` flag. The patient-facing `license_label`
    becomes `"CMP <license_number>"`. Patient search and availability (Part 3,
    4.2 and 4.3) now list only `VERIFIED` physicians. A physician user without a
    profile gets one created lazily as `PENDING`.

77. **Physician weight unit.** The review packet shows biometrics "in the
    physician's preference", but `PhysicianProfile` has no unit field. An
    internal `preferred_weight_unit` (default `KG`, admin-editable) is used and
    is not exposed in the API.

78. **Avatars.** Pillow is added as a dependency to decode, cap at 4000 px,
    strip metadata and re-encode images. Storage is private, so `avatar_url` is
    a pre-signed URL valid for `AVATAR_URL_TTL_SECONDS` (1 hour), issued at read
    time. A configured external `avatar_url` is still used when no upload
    exists. The throttle scope is `avatar_upload` (10/hour). An anonymous upload
    is tied to its invitation and can only be attached by that invitation's
    accept call.

79. **MFA (2.5).** TOTP is implemented in-house (RFC 6238: SHA-1, 30 s, 6 digits,
    ±1 step). The secret is encrypted at rest and the 10 backup codes are stored
    hashed and are single-use. The `mfa_token` is random, stored hashed, valid
    for 5 minutes and single-use. A wrong code returns `400 validation_error`.
    After 5 wrong codes, and for an expired or used token, the response is
    `401 token_invalid`. `MFA_REQUIRED_ROLES` (default `PHYSICIAN`, `STAFF`,
    `ADMIN`, `[OPEN]`) is enforced in the JWT authentication class. Until
    enrolment, only `GET /auth/me/`, `POST /auth/logout/` and the
    `/auth/mfa/totp/setup|confirm/` endpoints work; everything else returns
    `403 mfa_enrollment_required`. Google login returns the same MFA challenge
    when MFA is enabled. Disabling is `403 permission_denied` for mandatory
    roles, and running setup again after enrolment is `409 conflict`. The test
    settings set `MFA_REQUIRED_ROLES` to empty; the MFA tests turn it on.

80. **Invitations and specialties.** `PhysicianInvitation` and `Specialty` are
    created in the admin until Part 5 adds endpoints. The token is 32 random
    bytes, stored as a SHA-256 hash, and expires after
    `PHYSICIAN_INVITATION_TTL_DAYS` (7). If an account already exists for the
    invited email, accept returns `400 validation_error`. Accept requires the
    current `CLINICIAN_AGREEMENT`, which is seeded as a placeholder document
    (`[LEGAL REVIEW]`). Staff are told about new accounts and rejections by email
    to `SUPPORT_NOTIFICATION_EMAILS`, without PHI. Throttle scopes are
    `invitation_lookup` (20/hour) and `invitation_accept` (5/hour).

81. **Verification gate.** `403 credentials_not_verified`
    (`details.verification_status`) applies to `/clinical/*`,
    `/prescription-documents/*`, the dashboard, the calendar and clinician
    messaging, as in the section 9 table.

82. **Optimistic concurrency.** `Evaluation.version` starts at 1 and increases
    on every status change and decision. `If-Match` accepts `"3"`, `W/"3"` or
    `3`. Responses carry the new value in `ETag`.

83. **Queue, claims and visibility.** `Program.allowed_specialties` (empty
    means every specialty, `[OPEN]`) decides who sees an unclaimed `SUBMITTED`
    evaluation. `MINE` covers evaluations assigned to the caller in `SUBMITTED`
    (labs returned), `IN_REVIEW` and `PENDING_LABS`. `counts.all` is
    `queue + mine`. Claiming your own assigned `SUBMITTED` evaluation moves it to
    `IN_REVIEW` again. Claim activity (claim, packet read, decision) updates
    `last_activity_at`. A beat job releases claims idle for
    `EVALUATION_CLAIM_TIMEOUT_HOURS` (4). A release clears the assignee.

84. **Review packet.** Questions without a `review_group` fall into `OTHER`. The
    four Figma groups are always returned (they can be empty); `OTHER` is
    returned only when it has items. For a flagged `YES_NO` or `TEXT` answer
    there is no option, so `option_id` is `null`, `option_label` is the answer,
    and approval must acknowledge that `question_id` instead.
    `lab_recommendations` is the physician snapshot or the program panels.

85. **Lab-panel catalog (4.4).** This is the de-duplicated union (by `code`) of
    the active programs' admin-edited `lab_panels`. Request-labs replaces the
    evaluation snapshot (`recommended_by = PHYSICIAN`), resets the assessment's
    `labs_status` to `NOT_STARTED` so the patient is asked again, and stores the
    message as the patient-facing `patient_note`.

86. **Reject.** `rejection_reason_code` and the encrypted `internal_note` are
    stored on the evaluation. `patient_note` is the Part 2 patient-facing note.
    Staff get a PHI-free email. No refund is triggered (Part 5).

87. **Products and prescribing guardrails.** `Product` gains
    `active_ingredient`, `form`, `allowed_doses`, `starting_doses`,
    `default_frequency`, `max_refills` and `programs`. Semaglutide is seeded with
    starting dose 0.25 mg, allowed 0.25/0.5/1.0/1.7/2.4 mg and 5 refills
    (`[MEDICAL REVIEW]`). Doses match on value and unit. `day_of_week` is
    required for `WEEKLY` and `EVERY_TWO_WEEKS`.

88. **Approve.** The Part 2 `Prescription` (one per evaluation) gains
    `start_date`, `valid_until`, `issued_by` and `signed_document`. Each item
    becomes a `Treatment` with `started_at = start_date`,
    `prescription_expires_at = valid_until`, `instructions`, `duration_weeks`,
    `refills_total = refills_remaining`, and a version 1 entry. A signed document
    must be `CLEAN` and belong to the same evaluation; it is used as the PDF.
    Otherwise a Celery task writes an unsigned text PDF with a dependency-free
    generator (`[LEGAL REVIEW]` Peruvian e-prescription rules).
    `flags_not_acknowledged` returns `details.missing`.

89. **Prescription documents (4.8).** These follow the lab-upload rules (PDF
    magic bytes, 10 MB, antivirus scan, statuses `PENDING_UPLOAD`, `SCANNING`,
    `CLEAN`, `REJECTED`). Create returns `{id, upload}` and confirm returns
    `{id, status, content_type, size_bytes, created_at}`, since the contract
    does not specify the confirm shape.

90. **My Patients status.** `NEWLY_APPROVED` means an evaluation approved by
    this physician in the last 14 days with no delivered order. Otherwise the
    status follows the newest treatment's status (`ACTIVE` →
    `ACTIVE_TREATMENT`). A patient linked only through a consultation or an open
    evaluation has no such status, so `status` is `null` for them (the contract
    type is not nullable). `last_activity_at` is the latest of the evaluation,
    treatment, consultation and message timestamps. Contact details in the chart
    are shown only when the caller prescribed a treatment or owns an evaluation
    for that patient.

91. **Treatment versions.** `TreatmentVersion` is immutable. A version effective
    today is applied at once. Future ones are applied by the clinical beat job.
    The patient is notified with the new `TREATMENT_UPDATED` type, which section
    5.4 names but section 1 omits. Pause, resume, discontinue and renewal
    decisions use the same notification. Discontinuing cancels open orders that
    have not reached `COMPOUNDING` (refunding paid ones) and sets
    `Subscription.needs_staff_review`.

92. **Renewals.** `RenewalRequestStatus` gains `APPROVED` and `DENIED`.
    Approving adds `refills_added` to both the total and the remaining count
    (starting from 0 when the treatment had no refills tracked). `valid_until`
    must be after today and at most 12 months ahead. The patient's request
    sends `RENEWAL_REQUESTED` to the prescribing physician.

93. **Clinician consultations.** `reason` is the patient's note, or the agenda
    when there is no note. A physician reschedule keeps the status, records a
    history row, notifies the patient with `CONSULTATION_REMINDER` (the closest
    existing generic type) and sets `rescheduled_by_physician_at`, which lets the
    patient cancel at any time before the start. Physician cancellations record
    `cancelled_by`. A physician join returns an owner token, records
    `physician_joined_at` and moves `CONFIRMED` to `IN_PROGRESS`. The Daily
    webhook does the same when the joining `user_id` is the physician. Complete
    from `CONFIRMED` needs `physician_joined_at`. It ends the room through the
    video port and saves the notes as a `CLINICAL` note. `follow_up` is stored on
    the consultation, but Part 3's overview has no field for it, so patients do
    not see it yet (`[OPEN]`). Patient actions now notify the physician with
    `CONSULTATION_REQUESTED`, `CONSULTATION_CANCELLED_BY_PATIENT` and
    `CONSULTATION_RESCHEDULED_BY_PATIENT`.
    The decline `patient_note` is stored in the consultation history: the Part 3
    `Consultation` shape has no field to show it to the patient (`[OPEN]`).

94. **Availability.** Rules gain `kind`, `label` and
    `consultation_type_ids`. Blocks gain `kind` (`BLOCKED`,
    `EXTRA_AVAILABLE`) and `label`. Slots are built from `AVAILABLE` rules for
    the requested type plus `EXTRA_AVAILABLE` blocks, minus `BLOCKED` rules and
    blocks and open consultations. A change is rejected with
    `409 conflicts_with_consultations` only for future open consultations that
    were covered before the change and are not covered after it.
    `GET /time-blocks/` defaults to the next 30 days when `from`/`to` are
    omitted.

95. **Calendar.** `AVAILABLE_SLOT` items are free windows (availability minus
    blocks and consultations). The booking lead time is not applied, because
    this is the physician's own view.

96. **Clinician messaging.** A physician has a care relationship with a patient
    as in item 90. A nurse has one with the patients assigned to them. Clinician
    thread items use the patient participant shape from section 8. Other `STAFF`
    users get `403`. Unverified physicians get `403 credentials_not_verified`.

97. **New error codes.** `credentials_not_verified` (403),
    `mfa_enrollment_required` (403), `claim_required` (403),
    `already_claimed` (409), `overlapping_rule` (409),
    `conflicts_with_consultations` (409, `details.consultation_ids`),
    `precondition_failed` (412), `precondition_required` (428),
    `flags_not_acknowledged` (400), `dose_out_of_range` (400),
    `invitation_expired`, `invitation_revoked` and `invitation_already_used`
    (410).

98. **Figma review (Part 4, section 10).** Recorded in
    `docs/figma-screens-part4.md`; no API impact.
