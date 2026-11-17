# Adelgaceya backend

Production-grade Django 5.2 backend for **Adelgaceya**, a physician-supervised
telehealth platform for medical weight-loss and metabolic/longevity programs in
Peru. The system handles sensitive health data, so authorization, auditability
and privacy controls are treated as product features.

## Phase status

API contract Parts 1 (v1.2), 2, 3, 4 and 5 (v1.0) are implemented. See
`docs/contract-deviations.md` for the owner-review notes,
`docs/figma-screens-part1.md` to `docs/figma-screens-part5.md` for the Figma
review records, and `docs/open-items.md` for pending legal/clinical/content
decisions.

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Foundation + API Contract Part 1: settings, Docker, CI, custom user, JWT/Google auth, email verification, legal documents, consents, public content, contact, complaints book, errors, Swagger/ReDoc, WhiteNoise, health | **Complete** |
| 2 | API Contract Part 2: programs, resumable assessments, questionnaire, measurements/BMI, screening, summary/submit/review/cancel, evaluation fee + Culqi port, lab uploads + scanning gate, patient evaluation and prescription download; audit log and idempotency | **Complete** |
| 3 | API Contract Part 3: patient profile and addresses, overview, consultations and video, treatments, packages/checkout/orders, subscriptions, saved cards, signed Culqi/Daily webhooks, messaging, notifications | **Complete** |
| 4 | API Contract Part 4: physician invitations and MFA, review queue and decisions with prescribing, My Patients and notes, treatment adjustments and renewals, clinician consultations, availability and calendar, clinician messaging | **Complete** |
| 5 | API Contract Part 5: staff invitations and permissions, staff/admin dashboards, patients, evaluation assignment, support tickets, complaints, orders, subscriptions, refunds; admin products, programs, packages, questionnaires, content, reference data, legal versions, settings, reports and exports, audit log, privacy requests | **Complete** |
| 6 | Reports/analytics, performance/security hardening, final docs | Not started |

## Quick start (Docker, < 5 minutes)

Prerequisites: Docker Desktop/Engine with Compose v2.

```bash
git clone <repository-url> adelgaceya
cd adelgaceya
cp .env.example .env          # optional; compose has safe local defaults
./scripts/compose-up.sh       # or: make up (picks free ports, see below)
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
| Nurse (staff user, nurse profile) | `sarah.nurse@adelgaceya.pe` |
| Admin | `admin@adelgaceya.pe` |

> The demo password is public. Never run `seed_demo` against production.

**Ports are picked automatically.** `./scripts/compose-up.sh` (or `make up`)
tries each default port (API 8000, Postgres 5435, Redis 6381, MinIO
9000/9001, Mailpit 1025/8025). If one is already in use, it takes the next
free port, saves it to `.env` and prints the final URLs, for example
`API: http://<host>:8000/api/v1/`. A running stack keeps its ports. To force a
port, set it in `.env` (`WEB_PORT=8010`); it is used if free. Plain
`docker compose up` also works and uses whatever is in `.env`.

On a server reached by IP or domain, also set `DJANGO_ALLOWED_HOSTS` (and
`CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`) in `.env`,
otherwise Django answers `400 Bad Request`.

### Email (Zoho SMTP)

The compose stack uses Mailpit by default, so nothing leaves your machine. For real delivery,
put the SMTP account in `.env` (it is gitignored):

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.zoho.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=contacto@adelgaceya.pe
EMAIL_HOST_PASSWORD=<Zoho app password>
DEFAULT_FROM_EMAIL=Adelgaceya <contacto@adelgaceya.pe>
```

Then recreate the services that send mail (`web` handles requests synchronously, `worker` runs
the Celery email tasks):

```bash
docker compose up -d --force-recreate web worker beat
docker compose exec web python manage.py send_test_email contacto@adelgaceya.pe
```

Never commit `.env`; `.env.example` documents the fields with a placeholder password.

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
- [Figma screens for Part 1](docs/figma-screens-part1.md)
- [Figma screens for Part 2](docs/figma-screens-part2.md)
- [Open items and TODOs](docs/open-items.md)
- [Contributing](CONTRIBUTING.md)

## Repository layout

```
config/                  settings split, URLs, Celery, WSGI/ASGI, gunicorn
apps/core/               BaseModel, errors, request IDs, permissions, crypto, storage, audit, health
apps/accounts/           User, consents, JWT/Google auth, emails
apps/catalog/            Public site settings, FAQs, testimonials, stats
apps/support/            Contact messages and complaints book
apps/assessments/        Programs, questionnaires, assessments, evaluations, fees/payments, labs, prescriptions
templates/emails/        verification, reset and account-exists emails
tests/                   cross-cutting API and security tests
docs/                    architecture, security, runbook, ADRs
```

Every domain app follows the same shape: `models.py`, `serializers.py`,
`views.py`, `services.py` (writes/business rules), `selectors.py` (reads),
`permissions.py`, `filters.py`, `tasks.py`, `admin.py`, `tests/`.

## License

Proprietary. All rights reserved.
