"""Local development settings. Never use these in production."""

from __future__ import annotations

from .base import *

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ENVIRONMENT = "local"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "testserver"]
ENABLE_API_DOCS = True

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://127.0.0.1:3000"],
)
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000"],
)

if DEBUG:
    # No manifest is generated during development; use collectstatic for parity.
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    }
    WHITENOISE_AUTOREFRESH = True
    WHITENOISE_USE_FINDERS = True
else:
    # docker compose runs local settings with DEBUG=False to exercise the
    # production static pipeline without production-only secrets.
    STORAGES["staticfiles"] = {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }
    WHITENOISE_AUTOREFRESH = False
    WHITENOISE_USE_FINDERS = False

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

LOGGING["root"]["level"] = env("LOG_LEVEL", default="INFO")
