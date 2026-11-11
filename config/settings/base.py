"""Base settings shared by every environment.

Secrets and environment-specific values are read from environment variables
(``.env`` is loaded in local development only). Production settings validate
required values and fail fast during startup.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# ``.env`` is a developer convenience; containers receive real env vars.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-development-key-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "testserver"])

ENVIRONMENT = env("DJANGO_ENVIRONMENT", default="production")
SITE_NAME = env("SITE_NAME", default="Adelgaceya")
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:3000")
BACKEND_URL = env("BACKEND_URL", default="http://localhost:8000")
SITE_ID = 1

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Third party
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "django_filters",
    "axes",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "anymail",
    "csp",
    # Local apps
    "apps.core.apps.CoreConfig",
    "apps.accounts",
    "apps.catalog.apps.CatalogConfig",
    "apps.support.apps.SupportConfig",
    "apps.assessments.apps.AssessmentsConfig",
    "apps.patients.apps.PatientsConfig",
    "apps.consultations.apps.ConsultationsConfig",
    "apps.treatments.apps.TreatmentsConfig",
    "apps.payments.apps.PaymentsConfig",
    "apps.orders.apps.OrdersConfig",
    "apps.messaging.apps.MessagingConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.clinical.apps.ClinicalConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "axes.middleware.AxesMiddleware",
    "apps.core.middleware.AdminCSPMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database and cache
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5432/adelgaceya",
    )
}
if DATABASES["default"].get("ENGINE", "").endswith("postgresql"):
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].setdefault(
        "connect_timeout", env.int("DB_CONNECT_TIMEOUT", default=5)
    )
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

CACHES = {
    "default": {
        **env.cache("REDIS_URL", default="redis://localhost:6379/0"),
        "KEY_PREFIX": "adelgaceya",
        "TIMEOUT": 300,
    }
}

# ---------------------------------------------------------------------------
# Authentication and identity
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": "",
    "AUDIENCE": None,
    "ISSUER": None,
    "JWK_URL": None,
    "LEEWAY": 30,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "USER_AUTHENTICATION_RULE": "rest_framework_simplejwt.authentication.default_user_authentication_rule",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
    "TOKEN_USER_CLASS": "rest_framework_simplejwt.models.TokenUser",
    "JTI_CLAIM": "jti",
    "SLIDING_TOKEN_REFRESH_EXP_CLAIM": "refresh_exp",
}

AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = timedelta(minutes=env.int("AXES_COOLOFF_MINUTES", default=15))
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_HTTP_RESPONSE_CODE = 429
AXES_ENABLE_ADMIN = True
AXES_ENABLED = env.bool("AXES_ENABLED", default=True)
# Use the API error envelope for lockouts and keep the cool-off anchored to the
# first lockout so repeated attempts cannot extend it.
AXES_LOCKOUT_CALLABLE = "apps.accounts.lockout.axes_lockout_response"
AXES_ENABLE_RETRY_AFTER_HEADER = True
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False

# Allauth is used as the social-login engine (Google ID token verification).
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_STORE_TOKENS = False
LOGIN_REDIRECT_URL = "/"

GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="")
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
        "APP": {
            "client_id": GOOGLE_CLIENT_ID,
            "secret": GOOGLE_CLIENT_SECRET,
            "key": "",
        },
    }
}

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.ContractJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "apps.core.permissions.IsAuthenticatedAndActive",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPageNumberPagination",
    "PAGE_SIZE": 20,
    "DATETIME_FORMAT": "%Y-%m-%dT%H:%M:%SZ",
    "DATE_FORMAT": "%Y-%m-%d",
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("THROTTLE_ANON", default="100/day"),
        "user": env("THROTTLE_USER", default="1000/day"),
        "auth": env("THROTTLE_AUTH", default="5/min"),
        "password_reset": env("THROTTLE_PASSWORD_RESET", default="3/hour"),
        "email_verification": env("THROTTLE_EMAIL_VERIFICATION", default="3/hour"),
        "upload": env("THROTTLE_UPLOAD", default="20/hour"),
        "messaging": env("THROTTLE_MESSAGING", default="60/min"),
        "password_reset_email": env("THROTTLE_PASSWORD_RESET_EMAIL", default="3/hour"),
        "contact": env("THROTTLE_CONTACT", default="5/hour"),
        "contact_email": env("THROTTLE_CONTACT_EMAIL", default="3/hour"),
        "complaint": env("THROTTLE_COMPLAINT", default="5/hour"),
        "complaint_status": env("THROTTLE_COMPLAINT_STATUS", default="10/hour"),
        "assessment_create": env("THROTTLE_ASSESSMENT_CREATE", default="20/hour"),
        "assessment_measurements": env("THROTTLE_ASSESSMENT_MEASUREMENTS", default="120/min"),
        "assessment_submit": env("THROTTLE_ASSESSMENT_SUBMIT", default="5/hour"),
        "assessment_payment": env("THROTTLE_ASSESSMENT_PAYMENT", default="5/hour"),
        "lab_upload": env("THROTTLE_LAB_UPLOAD", default="20/hour"),
        "consultation_create": env("THROTTLE_CONSULTATION_CREATE", default="10/hour"),
        "order_create": env("THROTTLE_ORDER_CREATE", default="5/hour"),
        "invitation_lookup": env("THROTTLE_INVITATION_LOOKUP", default="20/hour"),
        "invitation_accept": env("THROTTLE_INVITATION_ACCEPT", default="5/hour"),
        "avatar_upload": env("THROTTLE_AVATAR_UPLOAD", default="10/hour"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Adelgaceya API",
    "DESCRIPTION": (
        "Physician-supervised telehealth API for medical weight loss and metabolic "
        "programs in Peru.\n\n"
        "## Authentication\n"
        "Obtain an access token from `POST /api/v1/auth/login/` or "
        "`POST /api/v1/auth/google/`, then send it as "
        "`Authorization: Bearer <access_token>`. Access tokens live 15 minutes. "
        "Refresh with `POST /api/v1/auth/token/refresh/`; refresh tokens rotate and "
        "the previous token is blacklisted. Never place PHI in query strings.\n\n"
        "## Errors\n"
        "Every error uses `{code, message, details, request_id}` and echoes "
        "`X-Request-ID`. Object-level access failures return 404 to avoid revealing "
        "the existence of clinical records."
    ),
    "VERSION": "1.2.0",
    "OAS_VERSION": "3.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "TAGS": [
        {"name": "auth", "description": "Registration, login, tokens and consents."},
        {"name": "accounts", "description": "Current user and account lifecycle."},
        {"name": "legal", "description": "Current legal documents."},
        {"name": "consents", "description": "Consent history and acceptance."},
        {"name": "public", "description": "Anonymous public content and forms."},
        {"name": "assessments", "description": "Programs, questionnaires and patient assessments."},
        {"name": "labs", "description": "Lab recommendations, uploads and downloads."},
        {"name": "evaluations", "description": "Evaluation status, fee, payment and prescription."},
        {"name": "patients", "description": "Profile, preferences, addresses and overview."},
        {"name": "consultations", "description": "Physicians, availability, booking and video."},
        {"name": "treatments", "description": "Treatments, prescriptions and renewals."},
        {"name": "orders", "description": "Packages, checkout and orders."},
        {"name": "subscriptions", "description": "Recurring deliveries."},
        {"name": "payments", "description": "Payment status and saved cards."},
        {"name": "webhooks", "description": "Signed provider callbacks."},
        {"name": "messages", "description": "Patient and care-team messaging."},
        {"name": "notifications", "description": "In-app notifications."},
        {"name": "physicians", "description": "Physician onboarding, profile and availability."},
        {"name": "clinical", "description": "Clinician review queue, patients and consultations."},
        {"name": "health", "description": "Liveness and readiness probes."},
    ],
    "SECURITY": [{"bearerAuth": []}],
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Short-lived JWT access token.",
            }
        }
    },
    "ENUM_NAME_OVERRIDES": {
        "UserRoleEnum": [
            ("PATIENT", "Patient"),
            ("PHYSICIAN", "Physician"),
            ("STAFF", "Staff"),
            ("ADMIN", "Administrator"),
        ],
        "ConsentDocumentTypeEnum": [
            ("TERMS_OF_SERVICE", "Terms of Service"),
            ("PRIVACY_POLICY", "Privacy Policy"),
            ("HEALTH_DATA_PROCESSING", "Health data processing"),
            ("CLINICIAN_AGREEMENT", "Clinician agreement"),
        ],
        "ComplaintDocumentTypeEnum": [
            ("DNI", "DNI"),
            ("CE", "Carné de extranjería"),
            ("PASSPORT", "Passport"),
            ("RUC", "RUC"),
        ],
        "ComplaintStatusEnum": "apps.support.models.ComplaintBookEntry.Status",
        "ContactMessageStatusEnum": "apps.support.models.ContactMessage.Status",
        "AssessmentStatusEnum": [
            ("DRAFT", "Draft"),
            ("SUBMITTED", "Submitted"),
            ("CANCELLED", "Cancelled"),
        ],
        "EvaluationStatusEnum": [
            ("SCREENED_OUT", "Screened out"),
            ("AWAITING_PAYMENT", "Awaiting payment"),
            ("SUBMITTED", "Submitted"),
            ("IN_REVIEW", "In review"),
            ("PENDING_LABS", "Pending labs"),
            ("APPROVED", "Approved"),
            ("NOT_SUITABLE", "Not suitable"),
            ("CANCELLED", "Cancelled"),
        ],
        "ScreeningResultEnum": [
            ("ELIGIBLE", "Eligible"),
            ("NEEDS_REVIEW", "Needs review"),
            ("NOT_ELIGIBLE", "Not eligible"),
        ],
        "SexEnum": [("MALE", "Male"), ("FEMALE", "Female"), ("OTHER", "Other")],
        "WeightUnitEnum": [("KG", "Kilograms"), ("LB", "Pounds")],
        "HeightUnitEnum": [("CM", "Centimetres"), ("IN", "Inches")],
        "BmiCategoryEnum": [
            ("UNDERWEIGHT", "Underweight"),
            ("HEALTHY", "Healthy"),
            ("OVERWEIGHT", "Overweight"),
            ("OBESE", "Obese"),
        ],
        "StepTypeEnum": [("MEASUREMENTS", "Measurements"), ("QUESTIONS", "Questions")],
        "QuestionTypeEnum": [
            ("SINGLE_CHOICE", "Single choice"),
            ("MULTI_CHOICE", "Multiple choice"),
            ("YES_NO", "Yes / no"),
            ("TEXT", "Text"),
        ],
        "LabUploadStatusEnum": [
            ("PENDING_UPLOAD", "Pending upload"),
            ("SCANNING", "Scanning"),
            ("CLEAN", "Clean"),
            ("REJECTED", "Rejected"),
        ],
        "LabRejectionReasonEnum": [
            ("VIRUS_DETECTED", "Virus detected"),
            ("INVALID_FILE_TYPE", "Invalid file type"),
            ("FILE_TOO_LARGE", "File too large"),
            ("UPLOAD_MISSING", "Upload missing"),
            ("UNREADABLE", "Unreadable"),
        ],
        "LabsStatusEnum": [
            ("NOT_STARTED", "Not started"),
            ("DEFERRED", "Deferred"),
            ("SUBMITTED", "Submitted"),
        ],
        "FeeStatusEnum": [
            ("NOT_REQUIRED", "Not required"),
            ("WAIVED", "Waived"),
            ("PENDING", "Pending"),
            ("PAID", "Paid"),
            ("REFUNDED", "Refunded"),
        ],
        "PaymentStatusEnum": "apps.assessments.enums.PaymentStatus",
        "PaymentPurposeEnum": "apps.assessments.enums.PaymentPurpose",
        "ConsultationStatusEnum": "apps.consultations.models.ConsultationStatus",
        "CareRoleEnum": "apps.consultations.models.CareRole",
        "TreatmentStatusEnum": "apps.treatments.models.TreatmentStatus",
        "DoseFrequencyEnum": "apps.treatments.models.DoseFrequency",
        "TimeOfDayEnum": "apps.treatments.models.TimeOfDay",
        "AdministrationRouteEnum": "apps.treatments.models.AdministrationRoute",
        "OrderStatusEnum": "apps.orders.models.OrderStatus",
        "SubscriptionStatusEnum": "apps.orders.models.SubscriptionStatus",
        "BillingTypeEnum": "apps.orders.models.BillingType",
        "BillingDocumentTypeEnum": "apps.orders.models.BillingDocumentType",
        "MessageSenderRoleEnum": "apps.messaging.models.MessageSenderRole",
        "NotificationTypeEnum": "apps.notifications.models.NotificationType",
        "AvailabilityKindEnum": "apps.consultations.models.AvailabilityKind",
        "TimeBlockKindEnum": "apps.consultations.models.TimeBlockKind",
        "NoteKindEnum": "apps.clinical.models.NoteKind",
        "PhysicianVerificationStatusEnum": "apps.consultations.models.PhysicianVerificationStatus",
        "InvitationStatusEnum": "apps.clinical.models.InvitationStatus",
        "RenewalRequestStatusEnum": "apps.treatments.models.RenewalRequestStatus",
        "RejectionReasonCodeEnum": "apps.assessments.enums.RejectionReasonCode",
        "ReviewGroupEnum": "apps.assessments.enums.ReviewGroup",
        "ChecklistStateEnum": [
            ("DONE", "Done"),
            ("ACTIVE", "Active"),
            ("PENDING", "Pending"),
            ("BLOCKED", "Blocked"),
        ],
        "JourneyStepEnum": [
            ("FIRST_ASSESSMENT", "First assessment"),
            ("SECOND_ASSESSMENT", "Second assessment"),
            ("ELIGIBILITY_REVIEW", "Eligibility and review"),
            ("PRESCRIPTION", "Prescription"),
            ("ORDER", "Order"),
        ],
        "YesNoEnum": [("YES", "Yes"), ("NO", "No")],
        "PaymentMethodEnum": [("CULQI", "Culqi")],
    },
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "filter": True,
        "docExpansion": "list",
    },
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "SERVE_PERMISSIONS": ["apps.core.permissions.CanAccessApiDocs"],
    "SERVE_AUTHENTICATION": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
}

ENABLE_API_DOCS = env.bool("ENABLE_API_DOCS", default=False)

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS: list[Path] = []
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {
        "BACKEND": env(
            "DEFAULT_FILE_STORAGE", default="django.core.files.storage.FileSystemStorage"
        )
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME", default="")
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", default="us-east-1")
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default="")
AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY", default="")
AWS_S3_ADDRESSING_STYLE = env("AWS_S3_ADDRESSING_STYLE", default="path")
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = env.int("AWS_QUERYSTRING_EXPIRE", default=300)
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None

if AWS_STORAGE_BUCKET_NAME:
    STORAGES["default"] = {
        "BACKEND": "apps.core.storage.PrivateS3Storage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "region_name": AWS_S3_REGION_NAME,
            "endpoint_url": AWS_S3_ENDPOINT_URL or None,
            "access_key": AWS_ACCESS_KEY_ID or None,
            "secret_key": AWS_SECRET_ACCESS_KEY or None,
            "addressing_style": AWS_S3_ADDRESSING_STYLE,
            "querystring_auth": True,
            "querystring_expire": AWS_QUERYSTRING_EXPIRE,
            "file_overwrite": False,
            "default_acl": None,
        },
    }

WHITENOISE_USE_FINDERS = False
WHITENOISE_AUTOREFRESH = False
WHITENOISE_MANIFEST_STRICT = True

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Adelgaceya <no-reply@adelgaceya.pe>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
EMAIL_SERVICE = env("EMAIL_SERVICE", default="apps.core.email.DjangoEmailService")
EMAIL_VERIFICATION_TIMEOUT = env.int("EMAIL_VERIFICATION_TIMEOUT", default=60 * 60 * 24)
PASSWORD_RESET_TIMEOUT = env.int("PASSWORD_RESET_TIMEOUT", default=60 * 60)

ANYMAIL = {
    "MAILGUN_API_KEY": env("MAILGUN_API_KEY", default=""),
    "MAILGUN_SENDER_DOMAIN": env("MAILGUN_SENDER_DOMAIN", default=""),
    "SENDGRID_API_KEY": env("SENDGRID_API_KEY", default=""),
    "POSTMARK_SERVER_TOKEN": env("POSTMARK_SERVER_TOKEN", default=""),
}

# ---------------------------------------------------------------------------
# Security, privacy and compliance
# ---------------------------------------------------------------------------
CONSENT_VERSIONS = {
    "TERMS_OF_SERVICE": env("CONSENT_TERMS_VERSION", default="2026-11-01"),
    "PRIVACY_POLICY": env("CONSENT_PRIVACY_VERSION", default="2026-11-01"),
    "HEALTH_DATA_PROCESSING": env("CONSENT_HEALTH_DATA_VERSION", default="2026-11-01"),
    "CLINICIAN_AGREEMENT": env("CONSENT_CLINICIAN_AGREEMENT_VERSION", default="2026-11-01"),
}

COMPLAINT_RESPONSE_SLA_HOURS = env.int("COMPLAINT_RESPONSE_SLA_HOURS", default=24)
SUPPORT_NOTIFICATION_EMAILS = env.list("SUPPORT_NOTIFICATION_EMAILS", default=[])
CAPTCHA_REQUIRED = env.bool("CAPTCHA_REQUIRED", default=False)
CAPTCHA_VERIFIER = env("CAPTCHA_VERIFIER", default="apps.core.captcha.NoopCaptchaVerifier")
CAPTCHA_SECRET_KEY = env("CAPTCHA_SECRET_KEY", default="")

# Fernet keys. The first key encrypts new values; all keys can decrypt, which
# permits rotation. Production must supply an explicit key list.
_field_keys = env.list("FIELD_ENCRYPTION_KEYS", default=[])
if not _field_keys:
    _digest = hashlib.sha256(SECRET_KEY.encode("utf-8")).digest()
    _field_keys = [base64.urlsafe_b64encode(_digest).decode("ascii")]
FIELD_ENCRYPTION_KEYS = _field_keys

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-request-id",
    "idempotency-key",
]
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'", "data:"],
        "connect-src": ["'self'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'none'"],
    }
}
CONTENT_SECURITY_POLICY_REPORT_ONLY = None

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Upload limits; the concrete 10 MB allow-list is enforced by the lab flow.
FILE_UPLOAD_MAX_MEMORY_SIZE = env.int("FILE_UPLOAD_MAX_MEMORY_SIZE", default=10 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int("DATA_UPLOAD_MAX_MEMORY_SIZE", default=11 * 1024 * 1024)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000
LAB_UPLOAD_MAX_BYTES = env.int("LAB_UPLOAD_MAX_BYTES", default=10 * 1024 * 1024)
LAB_UPLOAD_URL_TTL_SECONDS = env.int("LAB_UPLOAD_URL_TTL_SECONDS", default=15 * 60)
LAB_DOWNLOAD_URL_TTL_SECONDS = env.int("LAB_DOWNLOAD_URL_TTL_SECONDS", default=5 * 60)

# Integration ports. Production must replace these with real adapters before
# enabling payments or lab uploads.
PAYMENT_PROVIDER = env("PAYMENT_PROVIDER", default="apps.assessments.payments.FakePaymentProvider")

# ---------------------------------------------------------------------------
# Part 3: patient portal ([OPEN] values are configuration, not constants)
# ---------------------------------------------------------------------------
PORTAL_DEFAULT_TIMEZONE = env("PORTAL_DEFAULT_TIMEZONE", default="America/Lima")
ADDRESS_MAX_PER_PATIENT = env.int("ADDRESS_MAX_PER_PATIENT", default=10)
CONSULTATION_MIN_BOOKING_LEAD_HOURS = env.int("CONSULTATION_MIN_BOOKING_LEAD_HOURS", default=12)
CONSULTATION_RESCHEDULE_MIN_HOURS = env.int("CONSULTATION_RESCHEDULE_MIN_HOURS", default=24)
CONSULTATION_CANCEL_MIN_HOURS = env.int("CONSULTATION_CANCEL_MIN_HOURS", default=2)
CONSULTATION_JOIN_EARLY_MINUTES = env.int("CONSULTATION_JOIN_EARLY_MINUTES", default=10)
CONSULTATION_JOIN_GRACE_MINUTES = env.int("CONSULTATION_JOIN_GRACE_MINUTES", default=15)
CONSULTATION_REQUEST_EXPIRY_HOURS = env.int("CONSULTATION_REQUEST_EXPIRY_HOURS", default=2)
CONSULTATION_REMINDER_HOURS = env.list("CONSULTATION_REMINDER_HOURS", cast=int, default=[24, 1])
VIDEO_PROVIDER = env("VIDEO_PROVIDER", default="apps.consultations.video.FakeVideoProvider")
VIDEO_RECORDING_ENABLED = env.bool("VIDEO_RECORDING_ENABLED", default=False)
DAILY_API_KEY = env("DAILY_API_KEY", default="")
DAILY_DOMAIN = env("DAILY_DOMAIN", default="adelgaceya.daily.co")
DAILY_WEBHOOK_SECRET = env("DAILY_WEBHOOK_SECRET", default="")
CULQI_WEBHOOK_SECRET = env("CULQI_WEBHOOK_SECRET", default="")
WEBHOOK_MAX_BODY_BYTES = env.int("WEBHOOK_MAX_BODY_BYTES", default=256 * 1024)
ORDER_NUMBER_PREFIX = env("ORDER_NUMBER_PREFIX", default="ADL-ORD-")
ORDER_SHIPPING_FEE = env("ORDER_SHIPPING_FEE", default="0.00")
ORDER_TAX_RATE = env("ORDER_TAX_RATE", default="0")
ORDER_DELIVERY_DAYS_MIN = env.int("ORDER_DELIVERY_DAYS_MIN", default=2)
ORDER_DELIVERY_DAYS_MAX = env.int("ORDER_DELIVERY_DAYS_MAX", default=5)
SUBSCRIPTION_RETRY_DAYS = env.list("SUBSCRIPTION_RETRY_DAYS", cast=int, default=[1, 3, 7])
SUBSCRIPTION_PAUSE_MAX_DAYS = env.int("SUBSCRIPTION_PAUSE_MAX_DAYS", default=90)
PRESCRIPTION_DOWNLOAD_TTL_SECONDS = env.int("PRESCRIPTION_DOWNLOAD_TTL_SECONDS", default=5 * 60)

# ---------------------------------------------------------------------------
# Part 4: clinician side ([OPEN] values are configuration)
# ---------------------------------------------------------------------------
MFA_REQUIRED_ROLES = env.list("MFA_REQUIRED_ROLES", default=["PHYSICIAN", "STAFF", "ADMIN"])
MFA_ISSUER = env("MFA_ISSUER", default="Adelgaceya")
MFA_TOKEN_TTL_SECONDS = env.int("MFA_TOKEN_TTL_SECONDS", default=300)
MFA_MAX_ATTEMPTS = env.int("MFA_MAX_ATTEMPTS", default=5)
PHYSICIAN_INVITATION_TTL_DAYS = env.int("PHYSICIAN_INVITATION_TTL_DAYS", default=7)
AVATAR_MAX_BYTES = env.int("AVATAR_MAX_BYTES", default=5 * 1024 * 1024)
AVATAR_MAX_PIXELS = env.int("AVATAR_MAX_PIXELS", default=4000)
AVATAR_URL_TTL_SECONDS = env.int("AVATAR_URL_TTL_SECONDS", default=60 * 60)
EVALUATION_CLAIM_TIMEOUT_HOURS = env.int("EVALUATION_CLAIM_TIMEOUT_HOURS", default=4)
NEWLY_APPROVED_DAYS = env.int("NEWLY_APPROVED_DAYS", default=14)
PHYSICIAN_RESCHEDULE_MIN_HOURS = env.int("PHYSICIAN_RESCHEDULE_MIN_HOURS", default=1)
PRESCRIPTION_MAX_START_DAYS = env.int("PRESCRIPTION_MAX_START_DAYS", default=30)
PRESCRIPTION_MAX_VALIDITY_MONTHS = env.int("PRESCRIPTION_MAX_VALIDITY_MONTHS", default=12)
LAB_SCANNER = env("LAB_SCANNER", default="apps.assessments.scanning.NoopLabScanner")

# ---------------------------------------------------------------------------
# Async and observability
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/2")
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_CONNECTION_TIMEOUT = env.int("CELERY_BROKER_CONNECTION_TIMEOUT", default=5)
CELERY_TIMEZONE = "America/Lima"
CELERY_BEAT_SCHEDULE: dict[str, dict[str, object]] = {
    "consultations-housekeeping": {
        "task": "apps.consultations.tasks.run_consultation_jobs",
        "schedule": 300.0,
    },
    "clinical-housekeeping": {
        "task": "apps.clinical.tasks.run_clinical_jobs",
        "schedule": 900.0,
    },
    "subscriptions-renewals": {
        "task": "apps.orders.tasks.run_subscription_jobs",
        "schedule": 900.0,
    },
}

SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default=ENVIRONMENT)
SENTRY_RELEASE = env("SENTRY_RELEASE", default="")
SENTRY_TRACES_SAMPLE_RATE = env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "apps.core.logging.StructlogFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Access logs are emitted by gunicorn with a query-string-free format.
        "django.server": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "axes": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# Locale
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("es", "Spanish")]
TIME_ZONE = "America/Lima"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
