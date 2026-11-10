# Adelgaceya backend

Production-grade Django 5.2 backend for **Adelgaceya**, a physician-supervised
telehealth platform for medical weight-loss and metabolic/longevity programs in
Peru. The system handles sensitive health data, so authorization, auditability
and privacy controls are treated as product features.

## Phase status

API contract Part 1 (v1.1) is implemented. See `docs/contract-deviations.md`
for the owner-review notes and `docs/open-items.md` for pending legal/content
decisions.

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Foundation + API Contract Part 1: settings, Docker, CI, custom user, JWT/Google auth, email verification, legal documents, consents, public content, contact, complaints book, errors, Swagger/ReDoc, WhiteNoise, health | **Complete** |
| 2 | Profiles, RBAC, audit log, assessments, lab uploads + antivirus | Not started |
| 3 | Evaluations state machine, physician queue, consultations, availability, video | Not started |
| 4 | Catalog/content, treatments/refills, orders, subscriptions, Culqi payments | Not started |
| 5 | Messaging, tickets, contact, complaints book, notifications | Not started |
| 6 | Reports/analytics, performance/security hardening, final docs | Not started |

## Quick start (Docker, < 5 minutes)

Prerequisites: Docker Desktop/Engine with Compose v2.

```bash
git clone <repository-url> adelgaceya
cd adelgaceya
cp .env.example .env          # optional; compose has safe local defaults
docker compose up --build -d
docker compose exec web python manage.py seed_demo
```

Open:

- API: <http://localhost:8000/api/v1/>
- Swagger UI: <http://localhost:8000/api/docs/>
- ReDoc: <http://localhost:8000/api/redoc/>
- Admin: <http://localhost:8000/admin/>
- Mailpit: <http://localhost:8025/>
- MinIO console: <http://localhost:9001/> (`minioadmin` / `minioadmin`)

Demo accounts created by `seed_demo` (password `Adelgaceya2026!`):

| Role | Email |
| --- | --- |
| Patient | `paciente@adelgaceya.pe` |
| Physician | `marcus.vance@adelgaceya.pe` |
| Staff | `staff@adelgaceya.pe` |
| Admin | `admin@adelgaceya.pe` |

> The demo password is public. Never run `seed_demo` against production.

## Local development without Docker

Requires Python 3.12 and PostgreSQL 16 + Redis 7.

```bash
uv sync
cp .env.example .env
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py runserver
```

Celery (two terminals):

```bash
uv run celery -A config worker -l info
uv run celery -A config beat -l info
```

## Tests and quality gates

```bash
make coverage     # pytest + coverage gate (>= 85%)
make lint         # ruff check + format check
make typecheck    # mypy, django-stubs, drf-stubs
make schema       # OpenAPI validation, fails on warnings
make docker-check # production check --deploy, zero warnings
```

Tests run against PostgreSQL. Set `DATABASE_URL` if your local database is not
`postgres://postgres:postgres@localhost:5432/adelgaceya`.

## Documentation

- [Architecture](docs/architecture.md)
- [Security and privacy](docs/security.md)
- [Operations runbook](docs/runbook.md)
- [API conventions](docs/api-conventions.md)
- [ERD](docs/erd.md)
- [ADRs](docs/decisions/)
- [API contract deviations](docs/contract-deviations.md)
- [Open items and TODOs](docs/open-items.md)
- [Contributing](CONTRIBUTING.md)

## Repository layout

```
config/                  settings split, URLs, Celery, WSGI/ASGI, gunicorn
apps/core/               BaseModel, errors, request IDs, permissions, crypto, health
apps/accounts/           User, consents, JWT/Google auth, emails
templates/emails/        verification, reset and account-exists emails
tests/                   cross-cutting API and security tests
docs/                    architecture, security, runbook, ADRs
```

Every domain app follows the same shape: `models.py`, `serializers.py`,
`views.py`, `services.py` (writes/business rules), `selectors.py` (reads),
`permissions.py`, `filters.py`, `tasks.py`, `admin.py`, `tests/`.

## License

Proprietary. All rights reserved.
