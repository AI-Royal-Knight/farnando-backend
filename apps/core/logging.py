"""Structured logging, PII redaction and Sentry event scrubbing.

There is no such thing as a safe place to log PHI. This module makes the safe
path the default: structured key/value logs, a redaction processor that runs
before rendering, and a Sentry ``before_send`` hook that removes request
bodies, auth headers and known identifiers.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from structlog.stdlib import ProcessorFormatter

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}")
_REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = {
    "password",
    "password1",
    "password2",
    "new_password",
    "old_password",
    "token",
    "access",
    "refresh",
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "cookie",
    "cookies",
    "set_cookie",
    "secret",
    "api_key",
    "email",
    "user_email",
    "phone",
    "phone_number",
    "first_name",
    "last_name",
    "full_name",
    "name",
    "address",
    "dni",
    "document_number",
    "ssn",
    "birth_date",
    "date_of_birth",
    "notes",
    "clinical_notes",
    "health_summary",
    "symptoms",
    "medications",
    "diagnosis",
}

_SENSITIVE_PARTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "api_key",
)


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(part in normalized for part in _SENSITIVE_PARTS)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact values whose keys or shapes look sensitive."""

    if key is not None and _is_sensitive(key):
        return _REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _JWT_RE.sub(_REDACTED, value)
        value = _BEARER_RE.sub(_REDACTED, value)
        return _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    return value


def redaction_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    return {key: redact(value, key=key) for key, value in event_dict.items()}


def configure_structlog() -> None:
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redaction_processor,
    ]
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


class StructlogFormatter(ProcessorFormatter):
    """JSON formatter shared by structlog and stdlib loggers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault(
            "processors",
            [
                ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
        kwargs.setdefault(
            "foreign_pre_chain",
            [
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                redaction_processor,
            ],
        )
        super().__init__(*args, **kwargs)


def scrub_sentry_event(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Remove PHI/PII from an event before it leaves the process."""

    _ = hint
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                key: value
                for key, value in headers.items()
                if key.lower() not in {"authorization", "cookie", "set-cookie", "x-api-key"}
            }
    user = event.get("user")
    if isinstance(user, dict):
        for key in ("email", "username", "ip_address", "name"):
            user.pop(key, None)
    for key in ("extra", "contexts", "tags"):
        if key in event:
            event[key] = redact(event[key])
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, (dict, list)):
        event["breadcrumbs"] = redact(breadcrumbs)
    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values", []):
            if isinstance(value, dict):
                value["value"] = redact(value.get("value", ""))
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        logentry["message"] = redact(logentry.get("message", ""))
    event["message"] = redact(event.get("message", ""))
    return event


def configure_logging() -> None:
    configure_structlog()


# Convenience for modules importing ``logger`` from here.
logger = structlog.get_logger(__name__)
