# ADR-0008: Offline Swagger assets, manifest storage and CSP

- **Status:** Accepted
- **Date:** 2026-11-10

## Context

Swagger UI and ReDoc must work with `DEBUG=False` and without relying on a CDN
(privacy, availability, CSP). Django admin also needs its hashed static assets.
Strict CSP conflicts with the inline bootstrap scripts in Swagger UI and admin.

## Decision

- Serve Swagger UI/ReDoc assets from `drf-spectacular-sidecar` and
  `CompressedManifestStaticFilesStorage`.
- Run `collectstatic` during the Docker build and serve static files with
  WhiteNoise immediately after `SecurityMiddleware`.
- Keep the global CSP strict. Add `'unsafe-inline'` only:
  - on the docs responses via `csp_update`, and
  - on `/admin/` responses via a path-scoped middleware.
- Return 404 for docs when `ENABLE_API_DOCS=False`; when enabled require staff/
  admin (local development is open).
- Add a `DEBUG=False` smoke test that resolves hashed names from the manifest and
  asserts admin CSS and Swagger JS return 200.

## Consequences

- No CDN dependency and no third-party script origin in CSP.
- `collectstatic` is mandatory; the test suite runs it once per session.
- Admin/docs are the only inline-script exceptions; API responses remain strict.
- The schema endpoint is staff-only, so Swagger UI in production relies on a
  staff session (admin login) or an authenticated fetch. This is acceptable for
  an internal docs surface.
