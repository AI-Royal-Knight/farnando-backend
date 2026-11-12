# Operations runbook

## 1. Prerequisites and topology

- Web: Gunicorn (`config.gunicorn`) running `config.settings.production`.
- Worker + Beat: Celery with Redis broker. Beat runs exactly one replica.
- PostgreSQL 16 with automated backups and PITR where available.
- Redis 7 with persistence/AOF and authentication in production.
- Private S3-compatible bucket. MinIO is used only locally.
- Anymail-backed ESP for transactional email.
- Optional Sentry for errors/traces; never enable PII.

Health endpoints:

| Endpoint | Purpose | Auth |
| --- | --- | --- |
| `GET /healthz` | Liveness, no dependencies | none |
| `GET /readyz` | DB, cache, broker, storage | none |

`/readyz` returns 503 and only component booleans; it never returns exception
messages.

## 2. First deploy

1. Provision PostgreSQL, Redis, bucket and DNS/TLS.
2. Create a least-privilege DB role (not superuser) and a bucket policy that
   denies public access.
3. Store secrets in the platform secret manager: `DJANGO_SECRET_KEY`,
   `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`,
   `FIELD_ENCRYPTION_KEYS`, ESP credentials, `GOOGLE_CLIENT_ID/SECRET`,
   `SENTRY_DSN` (optional), `AWS_*`.
4. Set `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
   `CORS_ALLOWED_ORIGINS`, `FRONTEND_URL`.
5. Build and push the image. The build runs `collectstatic` with manifest
   storage; the runtime image is non-root.
6. Run `python manage.py migrate --noinput` **once** as a release job.
7. Start web, worker and beat. Verify `/healthz`, `/readyz`, admin login and one
   authenticated API request.
8. Run `python manage.py check --deploy` in the release pipeline.

### 2.1 Part 2 integrations

- `LAB_SCANNER` defaults to `NoopLabScanner`, which marks every upload `CLEAN`.
  Production **must** point it at the real ClamAV adapter before labs are
  enabled; otherwise the scan gate is cosmetic.
- `PAYMENT_PROVIDER` defaults to `FakePaymentProvider`. Production must use the
  real Culqi adapter once it exists. Part 2 has no verified webhook yet, so a
  synchronous success stays `PENDING`; do not treat it as paid.
- Real pre-signed lab uploads require S3-compatible private storage. With
  `FileSystemStorage` the upload URL is a dev-only media URL, not a pre-signed
  `PUT`.
- Limits and URL lifetimes: `LAB_UPLOAD_MAX_BYTES` (10 MB),
  `LAB_UPLOAD_URL_TTL_SECONDS` (900), `LAB_DOWNLOAD_URL_TTL_SECONDS` (300).
- Watch the `scan_lab_upload` queue. Uploads stay `SCANNING` until the worker
  finishes; a stuck queue blocks physician visibility and lab submission.

### 2.2 Part 3 integrations

- Set `CULQI_WEBHOOK_SECRET` and `DAILY_WEBHOOK_SECRET`; without them every
  webhook is rejected with `401`. Register `https://<api>/api/v1/webhooks/culqi/`
  and `https://<api>/api/v1/webhooks/video/daily/` with the providers.
- Point `PAYMENT_PROVIDER` and `VIDEO_PROVIDER` at the real adapters once
  implemented (both currently return `503`).
- Run Celery beat (one replica): `run_consultation_jobs` (5 min) and
  `run_subscription_jobs` (15 min).
- Load the full INEI table: `python manage.py load_ubigeo ubigeo.csv`
  (`code,name[,is_deliverable]`), then review delivery coverage in the admin.
- Failed webhook processing is kept on `WebhookEvent.processing_error`
  (admin, read-only). Fix the cause and replay by asking the provider to resend.
- Care-team data (clinician profiles, availability, nurse assignment, packages,
  products) is maintained in the Django admin until Parts 4 and 5.

### 2.3 Part 4 operations

- Invite a physician: admin → Physician invitations → Add. The token is shown
  once; send the link `<frontend>/physician-invitations/<token>`.
- Verify credentials: admin → Clinician profiles → set `verification_status`
  (the physician is notified).
- Configure `Program.allowed_specialties` and product guardrails before launch
  (`[MEDICAL REVIEW]`).
- Beat must run `run_clinical_jobs` (claims and dose versions).
- With MFA enforced, every staff/admin/physician account (including demo
  accounts) must enrol on first login.

### 2.4 Part 5 operations

- Migrations seed Part 5 reference data (ticket categories, lab panels,
  settings groups). Review `/admin/settings/{group}/` before launch; every
  change needs a `reason` and is audited with old and new values.
- Invite staff from `/admin/staff/`; members set their own password from the
  emailed link (7 days, `STAFF_INVITATION_TTL_DAYS`) and must enrol MFA.
- Beat must run `run_support_jobs` (hourly) to close tickets left `RESOLVED`
  for `support.auto_close_resolved_days`.
- Report exports and privacy access exports are written to private storage;
  links expire after `REPORT_EXPORT_URL_TTL_SECONDS` and
  `PRIVACY_EXPORT_TTL_DAYS`.
- Privacy deletion anonymises the account and cannot be undone; the retained
  records are listed in the request's `response_note`.

## 3. Migrations policy (expand/contract)

Never combine a destructive schema change with the code that stops using the
old column in one release.

1. **Expand:** add nullable columns/tables/indexes concurrently where possible.
2. **Backfill:** run an idempotent management command/task in batches; monitor
   locks and replication lag.
3. **Switch:** deploy code reading the new shape while still writing both if
   needed.
4. **Contract:** after at least one full release/rollback window, drop the old
   column/table in a separate release.

Rules:

- No destructive migration in the same release as the code that removes usage.
- Large indexes on PostgreSQL use `CREATE INDEX CONCURRENTLY` (manual/atomic
  false migration); review lock impact.
- Test restore + migration forward on a recent production-like snapshot before
  every risky release.
- `makemigrations --check --dry-run` must be clean.

## 4. Deploy and rollback

Deploy:

```bash
docker build -t registry.example/adelgaceya:${GIT_SHA} .
docker push registry.example/adelgaceya:${GIT_SHA}
# release job: run migrations, then roll web/worker; beat one replica
```

Rollback:

- Application: redeploy the previous image digest. Migrations are forward-only;
  if the previous image cannot read the new schema, roll forward with a fix
  instead of rolling back blindly.
- Run the previous image with the same environment; never roll back secrets
  unless the old image requires them.
- Verify `/readyz`, login, and one clinical read after rollback.

## 5. Backup and restore

- PostgreSQL: daily full backup + continuous WAL/PITR; encrypt backups; restrict
  access; test restore monthly.
- S3: versioning + lifecycle rules; enable object lock if required by policy.
- Redis: treat as ephemeral except for Celery queues; do not use as a system of
  record.
- Restore drill:
  1. Provision an isolated database.
  2. Restore the latest snapshot and replay WAL to a target timestamp.
  3. Run migrations and a smoke test (`/readyz`, login, list own data).
  4. Record RTO/RPO and delete the isolated environment.

## 6. Celery operations

- Check queue depth and worker liveness (`celery -A config inspect ping`,
  `celery -A config inspect active`).
- Tasks are idempotent and retry with backoff/jitter. Failed tasks after max
  retries should go to a dead-letter queue/monitoring alert (Phase 2+).
- Beat must run as a single replica to avoid duplicate schedules. Tasks still
  need idempotency because beat can restart.
- Do not put PHI/PII in task arguments; pass IDs and load inside the worker.
- Purge/requeue only after understanding idempotency semantics.

## 7. Common issues

| Symptom | Check |
| --- | --- |
| `/readyz` 503 with `database=false` | DB host/credentials, connection limit, PgBouncer |
| `/readyz` 503 with `broker=false` | Redis URL, TLS, firewall, broker credentials |
| `DisallowedHost` | `DJANGO_ALLOWED_HOSTS`, proxy `Host` header |
| CSRF failure on admin | `CSRF_TRUSTED_ORIGINS`, `SECURE_PROXY_SSL_HEADER` |
| Static 404 in production | `collectstatic` ran at build? `STATIC_ROOT` mounted? manifest present? |
| Swagger assets 404 | `drf-spectacular-sidecar` installed and collectstatic run |
| Docs 404 | `ENABLE_API_DOCS=False` (expected in production by default) |
| Docs 401/403 for staff | staff user must be authenticated; session or JWT |
| Emails not sent | ESP credentials, `EMAIL_BACKEND`, Celery worker, Mailpit locally |
| Login locked | django-axes cool-off; inspect admin/axes records |
| Migration lock/timeout | check `pg_stat_activity`, long transactions, index strategy |

## 8. Connection pooling

- Django `CONN_MAX_AGE=60` is suitable for direct PostgreSQL connections.
- With PgBouncer in **transaction** mode, set `CONN_MAX_AGE=0` and disable
  server-side cursors; Django connections cannot be pinned across transactions.
- Size web workers + threads against the DB `max_connections`; Gunicorn workers
  are bounded by `WEB_CONCURRENCY` (default `2*CPU+1`, capped at 8).

## 9. Security incident

1. Preserve evidence; do not delete logs.
2. Rotate exposed secrets and revoke refresh tokens for affected users.
3. Assess scope (which records, which users) using audit logs.
4. Notify legal/privacy lead; **LEGAL_REVIEW** statutory notifications.
5. Patch, test, deploy; document the incident and follow-up actions.

## 10. Local operations

```bash
make up            # build + start compose stack
make logs          # follow web/worker
make migrate       # local database
make seed          # demo users
make down          # stop stack
docker compose down -v  # also delete local data volumes
```
