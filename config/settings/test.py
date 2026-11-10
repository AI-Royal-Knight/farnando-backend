"""Test settings.

Tests run against PostgreSQL (not SQLite) so constraints, indexes and
transaction behaviour match production. Redis/Celery are replaced with
in-process equivalents; the readiness probe is tested with mocks.
"""

from __future__ import annotations

from .base import *

DEBUG = False
ENVIRONMENT = "test"
SECRET_KEY = "test-secret-key-not-for-production"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
ENABLE_API_DOCS = True
SITE_ID = 1

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5432/adelgaceya",
    )
}
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"].pop("OPTIONS", None)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "adelgaceya-tests",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_IGNORE_RESULT = True

AXES_ENABLED = False

DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
MEDIA_ROOT = BASE_DIR / ".media-test"
STATIC_ROOT = BASE_DIR / ".staticfiles-test"
STATIC_URL = "/static/"
WHITENOISE_USE_FINDERS = False
WHITENOISE_AUTOREFRESH = False
WHITENOISE_MANIFEST_STRICT = True

CORS_ALLOWED_ORIGINS = ["http://localhost:3000"]
CSRF_TRUSTED_ORIGINS = ["http://testserver", "http://localhost:3000"]

FIELD_ENCRYPTION_KEYS = ["tW8uBgbY93Ih64CaqRsEIDheNMVimjTE97KQKAyVAxA="]

SENTRY_DSN = ""
SENTRY_TRACES_SAMPLE_RATE = 0.0

CONSENT_VERSIONS = {
    "TERMS": "2026-01",
    "PRIVACY": "2026-01",
    "HEALTH_DATA": "2026-01",
}

FRONTEND_URL = "http://localhost:3000"
GOOGLE_CLIENT_ID = "test-google-client-id.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "test-google-client-secret"
SOCIALACCOUNT_PROVIDERS["google"]["APP"] = {
    "client_id": GOOGLE_CLIENT_ID,
    "secret": GOOGLE_CLIENT_SECRET,
    "key": "",
}

PASSWORD_RESET_TIMEOUT = 3600
EMAIL_VERIFICATION_TIMEOUT = 3600

LOGGING["root"]["level"] = "WARNING"
LOGGING["loggers"]["django"]["level"] = "WARNING"
