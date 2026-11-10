# ADR-0007: REST + polling for messaging v1

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Messaging must work now for patient↔physician/nurse threads. Real-time
WebSockets add Channels, ASGI deployment, connection scaling and auth
complexity. The product does not require sub-second delivery for clinical
messaging; a few seconds is acceptable.

## Decision

Ship v1 as REST with:

- cursor pagination on `created_at`/id,
- a `since`/`after` polling parameter and ETag/conditional responses,
- read receipts stored as timestamps,
- attachments uploaded via pre-signed URLs.

The schema uses `MessageThread`, `Message`, `MessageAttachment` and participant
rows that are channel-compatible. No schema change is needed to add Channels.

## Consequences

- Simpler operations and no sticky WebSocket sessions.
- Polling must be throttled (`messaging` scope) and clients must back off;
  recommended interval ≥ 3 seconds with ETag.
- Notification latency is seconds, not milliseconds. If product requirements
  change, add Channels + Redis channel layer behind the same service methods.
- Clinical messaging is not for emergencies; the UI must state this and offer
  emergency guidance. **CLINICAL_REVIEW.**
