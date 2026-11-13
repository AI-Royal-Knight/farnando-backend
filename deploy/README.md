# Sample deployment: Render

This is a **sample** blueprint, not the selected production target (the hosting
decision is still open). It demonstrates the expected shape: one web service, a
Celery worker, a singleton beat worker, managed PostgreSQL 16 and Redis, and
secrets injected by the platform. Object storage is external (S3-compatible)
because Render does not provide a private S3 bucket.

Why Render:

- Docker-based deploy matches the tested image exactly.
- Managed PostgreSQL/Redis with private networking.
- Pre-deploy command runs migrations exactly once.
- Health checks map directly to `/readyz`.
- Simple rollback by image digest; no Kubernetes operator required.

Before using it:

1. Replace `owner`, region and plan values.
2. Set every `sync: false` secret in the dashboard.
3. Create the private bucket and restrict public access.
4. Confirm the region meets the Ley N.° 29733 transfer review.
5. Replace the sample domain and CSRF/CORS origins.

```yaml
databases:
  - name: adelgaceya-db
    databaseName: adelgaceya
    user: adelgaceya
    plan: starter
    postgresMajorVersion: "16"
    region: oregon

services:
  - type: redis
    name: adelgaceya-redis
    plan: starter
    maxmemoryPolicy: noeviction
    region: oregon

  - type: web
    name: adelgaceya-api
    runtime: docker
    plan: starter
    region: oregon
    healthCheckPath: /readyz
    preDeployCommand: python manage.py migrate --noinput
    envVars:
      - key: DJANGO_SETTINGS_MODULE
        value: config.settings.production
      - key: DJANGO_ALLOWED_HOSTS
        value: api.adelgaceya.pe
      - key: CSRF_TRUSTED_ORIGINS
        value: https://app.adelgaceya.pe
      - key: CORS_ALLOWED_ORIGINS
        value: https://app.adelgaceya.pe
      - key: FRONTEND_URL
        value: https://app.adelgaceya.pe
      - key: ENABLE_API_DOCS
        value: "False"
      - key: DJANGO_SECRET_KEY
        sync: false
      - key: DATABASE_URL
        fromDatabase:
          name: adelgaceya-db
          property: connectionString
      - key: REDIS_URL
        fromService:
          type: redis
          name: adelgaceya-redis
          property: connectionString
      - key: CELERY_BROKER_URL
        fromService:
          type: redis
          name: adelgaceya-redis
          property: connectionString
      - key: FIELD_ENCRYPTION_KEYS
        sync: false
      - key: AWS_STORAGE_BUCKET_NAME
        sync: false
      - key: AWS_S3_REGION_NAME
        sync: false
      - key: AWS_ACCESS_KEY_ID
        sync: false
      - key: AWS_SECRET_ACCESS_KEY
        sync: false
      - key: EMAIL_BACKEND
        value: anymail.backends.mailgun.EmailBackend
      - key: MAILGUN_API_KEY
        sync: false
      - key: MAILGUN_SENDER_DOMAIN
        sync: false
      - key: GOOGLE_CLIENT_ID
        sync: false
      - key: GOOGLE_CLIENT_SECRET
        sync: false
      - key: SENTRY_DSN
        sync: false
      - key: CAPTCHA_VERIFIER
        value: apps.core.captcha.TurnstileCaptchaVerifier
      - key: CAPTCHA_SECRET_KEY
        sync: false
      - key: CAPTCHA_REQUIRED
        value: "True"
      - key: PAYMENT_PROVIDER
        value: apps.assessments.payments.CulqiPaymentProvider
      - key: SUPPORT_NOTIFICATION_EMAILS
        sync: false
      - key: COMPLAINT_RESPONSE_SLA_HOURS
        value: "24"
      - key: WEB_CONCURRENCY
        value: "3"

  - type: worker
    name: adelgaceya-worker
    runtime: docker
    plan: starter
    region: oregon
    dockerCommand: celery -A config worker -l info --concurrency=2
    envVars:
      - fromGroup: adelgaceya-app

  - type: worker
    name: adelgaceya-beat
    runtime: docker
    plan: starter
    region: oregon
    dockerCommand: celery -A config beat -l info
    envVars:
      - fromGroup: adelgaceya-app
```

`envGroups` are omitted for brevity; put the shared application variables in an
`adelgaceya-app` group rather than duplicating them. The beat service must have
exactly **one** replica.
