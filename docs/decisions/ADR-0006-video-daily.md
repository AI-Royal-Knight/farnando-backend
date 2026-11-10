# ADR-0006: Daily.co as the default video adapter

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Physician consultations require video with per-consultation access control. The
provider must support short-lived room/token issuance and a fake for CI.

## Decision

Define a `VideoProvider` port with `create_room`, `issue_token` and `end_room`.
Provide `DailyVideoProvider` and `FakeVideoProvider`.

- A room and token are scoped to exactly one `Consultation`, with an expiry at
  or just after the scheduled end.
- Tokens are issued only to the patient and the assigned physician; staff do not
  receive clinical video tokens by default.
- Room creation and token issuance are audited.
- Provider API keys live in the secret manager; no provider SDK is imported
  outside the adapter.

## Consequences

- Provider outage handling: show a clear message, allow reschedule; do not fall
  back to unauthenticated links.
- Recording is disabled by default. If enabled later, it requires explicit
  patient consent, retention rules and legal review.
- Daily's data-processing terms and region must be reviewed before launch
  (**LEGAL_REVIEW**).
