"""Production settings: secure by default and fail fast on misconfiguration."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False
ENVIRONMENT = "production"

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
if not SECRET_KEY or SECRET_KEY == "insecure-development-key-change-me":
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a strong random value in production."
    )


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"Required environment variable {name} is missing.")
    return value


_require("DJANGO_ALLOWED_HOSTS")
_require("DATABASE_URL")
_require("REDIS_URL")
_require("CELERY_BROKER_URL")
_require("AWS_STORAGE_BUCKET_NAME")
_require("FIELD_ENCRYPTION_KEYS")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
if "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Wildcard ALLOWED_HOSTS is not permitted in production.")

for key in FIELD_ENCRYPTION_KEYS:
    try:
        Fernet(key.encode("ascii"))
    except Exception as exc:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS contains an invalid Fernet key.") from exc

if "console" in EMAIL_BACKEND or "locmem" in EMAIL_BACKEND:
    raise ImproperlyConfigured("A real transactional email backend is required in production.")

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if not CSRF_TRUSTED_ORIGINS:
    raise ImproperlyConfigured("CSRF_TRUSTED_ORIGINS must list the production frontend origins.")

ENABLE_API_DOCS = env.bool("ENABLE_API_DOCS", default=False)
API_DOCS_PUBLIC = False  # production docs always require staff/admin
if str(PAYMENT_PROVIDER).endswith("FakePaymentProvider"):
    raise ImproperlyConfigured("PAYMENT_PROVIDER must be a real provider in production.")
if env.bool("PAYMENTS_AUTO_CONFIRM", default=False):
    raise ImproperlyConfigured("PAYMENTS_AUTO_CONFIRM is for local development only.")
PAYMENTS_AUTO_CONFIRM = False
if str(PAYMENT_PROVIDER).endswith("CulqiPaymentProvider") and not env(
    "CULQI_SECRET_KEY", default=""
):
    raise ImproperlyConfigured("CULQI_SECRET_KEY is required with the Culqi payment provider.")
CAPTCHA_REQUIRED = env.bool("CAPTCHA_REQUIRED", default=True)
if CAPTCHA_REQUIRED:
    _require("CAPTCHA_SECRET_KEY")
    if str(CAPTCHA_VERIFIER).endswith("NoopCaptchaVerifier"):
        raise ImproperlyConfigured(
            "A real CAPTCHA_VERIFIER is required in production when CAPTCHA_REQUIRED is true."
        )

SECURE_SSL_REDIRECT = True
CONTENT_SECURITY_POLICY["DIRECTIVES"]["upgrade-insecure-requests"] = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
USE_X_FORWARDED_HOST = True

# Docs are staff-only when enabled; the dedicated views in apps.core.docs
# return 404 when ENABLE_API_DOCS is false.

SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default="production")
SENTRY_RELEASE = env("SENTRY_RELEASE", default="")
SENTRY_TRACES_SAMPLE_RATE = env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0)

if SENTRY_DSN:
    from apps.core.observability import init_sentry

    init_sentry(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE or None,
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
    )
