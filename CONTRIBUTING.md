# Contributing

## Setup

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run pre-commit install
```

Tests require PostgreSQL 16 at `DATABASE_URL` (default
`postgres://postgres:postgres@localhost:5432/adelgaceya`). Redis is only needed
for real Celery runs; tests use eager/in-memory modes.

## Before opening a pull request

```bash
make lint
make typecheck
make coverage
make schema
```

All four must pass. CI also runs migration drift, `check --deploy`, pip-audit,
bandit, gitleaks and a Docker build.

## Architecture rules

- Views are thin. Business logic belongs in `services.py`; read scoping belongs
  in `selectors.py`. Serializers validate shape, not policy.
- Never trust client-supplied role, ownership, price, BMI, eligibility or status.
- Every new endpoint needs:
  - explicit permission classes and object scoping,
  - a role × endpoint test,
  - OpenAPI documentation with `@extend_schema`,
  - documented 400/401/403/404/409/429 responses where applicable,
  - query-count coverage for lists (from Phase 2).
- Objects outside a caller's scope return 404, not 403.
- Clinical reads/writes must go through the audit service once Phase 2 lands.
- Do not log request bodies, emails, names, phone numbers, clinical notes or
  tokens. Celery arguments must be IDs, not payloads.
- New dependencies require an ADR and a pip-audit/security review.

## Commits and phases

Work in phases from the project plan. Each phase must end with green tests,
ruff, mypy, OpenAPI validation, `check --deploy`, and updated docs. Commit after
each phase; do not commit secrets or `.env` files.

## Review checklist

- [ ] Tests cover success, validation, auth, permission and throttle paths.
- [ ] No PHI in logs, errors, Sentry or task args.
- [ ] OpenAPI has no warnings.
- [ ] Migration is reversible or has a documented expand/contract plan.
- [ ] Docs and `docs/open-items.md` updated for unfinished work.
