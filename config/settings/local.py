"""Local development settings. Never use these in production."""

from __future__ import annotations

from .base import *

DEBUG = env.bool("DJANGO_DEBUG", default=True)
ENVIRONMENT = "local"
# Local hosts always work; DJANGO_ALLOWED_HOSTS adds more (e.g. a server IP
# when the compose stack runs on a remote machine).
ALLOWED_HOSTS = list(
    dict.fromkeys(
        [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "testserver",
            *env.list("DJANGO_ALLOWED_HOSTS", default=[]),
        ]
    )
)
ENABLE_API_DOCS = True
# The compose stack runs these settings with DEBUG=False; keep Swagger/ReDoc
# open there anyway. Set API_DOCS_PUBLIC=False to require a staff login.
API_DOCS_PUBLIC = env.bool("API_DOCS_PUBLIC", default=True)

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
)
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
    ],
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

# The fake payment provider never sends webhooks, so settle its charges immediately.
PAYMENTS_AUTO_CONFIRM = env.bool("PAYMENTS_AUTO_CONFIRM", default=True)

# Local only: let the patient portal settle the evaluation fee without a card ("Skip payment").
DEMO_PAYMENTS_ENABLED = env.bool("DEMO_PAYMENTS_ENABLED", default=True)

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

LOGGING["root"]["level"] = env("LOG_LEVEL", default="INFO")
