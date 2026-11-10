"""Sentry wiring is optional, explicit and never disables scrubbing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from apps.core.logging import scrub_sentry_event
from apps.core.observability import init_sentry, sentry_options


def test_init_sentry_is_noop_without_dsn() -> None:
    assert init_sentry(dsn="", environment="test", release=None) is False


def test_init_sentry_installs_scrubber() -> None:
    with patch("sentry_sdk.init") as init:
        assert init_sentry(
            dsn="https://key@example.ingest.sentry.io/1",
            environment="production",
            release="abc123",
            traces_sample_rate=0.1,
        )
    kwargs = init.call_args.kwargs
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["include_local_variables"] is False
    assert kwargs["before_send"] is scrub_sentry_event
    assert kwargs["release"] == "abc123"


def test_sentry_options_reads_settings() -> None:
    settings = SimpleNamespace(
        SENTRY_DSN="https://key@example.ingest.sentry.io/1",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="rel",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
    )
    assert sentry_options(settings) == {
        "dsn": "https://key@example.ingest.sentry.io/1",
        "environment": "staging",
        "release": "rel",
        "traces_sample_rate": 0.25,
    }
