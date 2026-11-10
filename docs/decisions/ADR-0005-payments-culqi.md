# ADR-0005: Culqi as the default payment adapter

- **Status:** Accepted (provider contract **LEGAL_REVIEW**)
- **Date:** 2026-11-10

## Context

The platform charges patients in Peru in PEN and, for international patients,
USD. The fixed decision names Culqi as the default, with a Fake adapter for
tests/local. Card data must never touch our servers.

## Decision

Define a `PaymentProvider` port with `create_payment`, `refund`,
`verify_webhook` and `parse_event`. Provide:

- `CulqiPaymentProvider` as the production adapter.
- `FakePaymentProvider` for tests and local development.

Implementation rules (Phase 4):

- The server computes the amount from catalog prices; client amounts are ignored.
- Webhooks verify the signature/timestamp before parsing.
- Raw events are stored once with a unique provider event id; processing is
  idempotent and tolerates out-of-order delivery.
- Subscriptions renew via Celery Beat with retry/dunning; every retry is
  idempotent.
- Card details are tokenised/hosted by the provider; no PAN, CVV or expiry is
  stored or logged.

## Consequences

- Provider SDK imports are confined to the adapter; domain code depends on a
  protocol.
- Culqi's webhook signature scheme and supported currencies must be confirmed
  against the signed contract before launch (**LEGAL_REVIEW**).
- Refunds and chargebacks need an operator UI and audit trail in Phase 4.
