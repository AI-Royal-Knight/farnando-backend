# Contract deviations and interpretation notes — Part 1 (v1.1)

Status: implementation notes for owner review. The contract is treated as the
source of truth. Where the contract is internally ambiguous, the literal
behaviour is implemented unless noted. No endpoint, field or enum is renamed.

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
    with `400 unknown_field`, including nested consent items.
