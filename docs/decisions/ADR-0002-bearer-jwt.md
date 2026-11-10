# ADR-0002: Bearer JWTs for the SPA, not httpOnly session cookies

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

The frontend is a separate SPA (and potentially native clients). The API must
support short-lived access and revocable refresh sessions. Two common options:

1. Bearer JWT in the `Authorization` header.
2. httpOnly, Secure, SameSite cookies plus CSRF protection.

## Decision

Use **bearer access tokens (15 min)** and **rotating refresh tokens (7 days)**
with blacklisting. The SPA keeps tokens in memory (or platform secure storage),
sends the access token explicitly, and re-authenticates after a full reload.
Cookies remain only for Django admin/docs sessions.

## Rationale

- No ambient cookie credentials, so cross-site request forgery against the API
  is removed; CORS is an explicit allow-list without credentials.
- Works identically for SPA, mobile and machine clients.
- Rotation + blacklist limits refresh-token replay; logout and password reset
  revoke all outstanding refresh tokens.
- A same-origin BFF/cookie mode can be added later without changing the token
  model; it would require CSRF and a same-site deployment.

## Consequences

- XSS is the critical risk. Mitigations: strict CSP (no third-party scripts),
  no PHI in browser storage, short access lifetime, React/SPA escaping, and
  dependency hygiene. This must be re-reviewed before launch.
- Access tokens cannot be revoked before expiry. Sensitive operations in later
  phases should require a recent authentication timestamp.
- `localStorage` is explicitly discouraged; documentation states the storage
  requirement.
