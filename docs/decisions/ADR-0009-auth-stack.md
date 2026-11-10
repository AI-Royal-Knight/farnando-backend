# ADR-0009: Custom JWT endpoints + django-allauth for Google

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

The fixed stack requires SimpleJWT, django-allauth for Google OAuth, email
verification and password reset. We could wire dj-rest-auth's generic endpoints,
but the product needs enumeration-resistant registration, consent recording,
custom email copy and a role model that dj-rest-auth does not know about.

## Decision

Implement the auth API directly with SimpleJWT and Django's password/token
primitives:

- custom register/login/refresh/logout/verify/reset views and serializers,
- signed, expiring tokens for email verification and password reset,
- django-allauth used as the **Google ID-token verification and social-account
  engine** inside `GoogleLoginService`,
- no `dj-rest-auth` dependency, because its generic views would be reimplemented
  anyway and it would expand the auth attack surface without adding value.

## Consequences

- Full control over uniform errors, consent capture and response shapes.
- allauth still owns Google provider details, `SocialAccount` and
  `EmailAddress` records, avoiding a bespoke OAuth implementation.
- We must maintain the small amount of auth code ourselves and keep it covered by
  tests. The ADR will be revisited if a second social provider is added.
