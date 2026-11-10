"""Optional Sentry initialisation with hard PII guarantees."""

from __future__ import annotations

from typing import Any

import structlog

from apps.core.logging import scrub_sentry_event

logger = structlog.get_logger(__name__)


def init_sentry(
    *,
    dsn: str,
    environment: str,
    release: str | None,
    traces_sample_rate: float = 0.0,
) -> bool:
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        before_send=scrub_sentry_event,  # type: ignore[arg-type]
        traces_sample_rate=traces_sample_rate,
    )
    logger.info("sentry_initialised", environment=environment, release=release)
    return True


def sentry_options(settings: Any) -> dict[str, Any]:
    dsn = getattr(settings, "SENTRY_DSN", "")
    return {
        "dsn": dsn,
        "environment": getattr(settings, "SENTRY_ENVIRONMENT", "production"),
        "release": getattr(settings, "SENTRY_RELEASE", None),
        "traces_sample_rate": getattr(settings, "SENTRY_TRACES_SAMPLE_RATE", 0.0),
    }
