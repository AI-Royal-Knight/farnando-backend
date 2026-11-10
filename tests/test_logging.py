"""Log redaction and Sentry scrubbing must never emit PHI/PII."""

from __future__ import annotations

from apps.core.logging import redact, scrub_sentry_event


def test_redact_removes_identifiers_and_secrets() -> None:
    payload = {
        "event": "user profile",
        "email": "ana@example.com",
        "user_email": "ana@example.com",
        "password": "secret",
        "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
        "nested": {
            "phone": "+51999999999",
            "clinical_notes": "Paciente con hipertensión",
            "address": "Av. Siempre Viva 123",
        },
        "message": "Contact ana@example.com or use Bearer abcdefghijklmnop",
    }
    result = redact(payload)

    assert result["email"] == "[REDACTED]"
    assert result["password"] == "[REDACTED]"
    assert result["access_token"] == "[REDACTED]"
    assert result["nested"]["phone"] == "[REDACTED]"
    assert result["nested"]["clinical_notes"] == "[REDACTED]"
    assert result["nested"]["address"] == "[REDACTED]"
    assert "ana@example.com" not in result["message"]
    assert "abcdefghijklmnop" not in result["message"]


def test_redact_handles_lists_and_non_strings() -> None:
    result = redact({"items": [{"email": "x@y.com"}, 3, None, "ok"]})
    assert result == {"items": [{"email": "[REDACTED]"}, 3, None, "ok"]}


def test_sentry_scrubber_removes_request_bodies_headers_and_user() -> None:
    event = {
        "message": "failure for ana@example.com",
        "request": {
            "data": {"password": "secret", "email": "ana@example.com"},
            "cookies": {"sessionid": "abc"},
            "query_string": "email=ana@example.com",
            "headers": {
                "Authorization": "Bearer abcdefghijklmnop",
                "Cookie": "sessionid=abc",
                "User-Agent": "pytest",
            },
        },
        "user": {"id": "uuid", "email": "ana@example.com", "ip_address": "127.0.0.1"},
        "extra": {"email": "ana@example.com", "notes": "dato clínico"},
        "exception": {"values": [{"type": "ValueError", "value": "failed for ana@example.com"}]},
        "logentry": {"message": "contact ana@example.com"},
    }
    result = scrub_sentry_event(event, {})
    assert result is not None
    serialised = str(result)
    assert "ana@example.com" not in serialised
    assert "dato clínico" not in serialised
    assert "abcdefghijklmnop" not in serialised
    assert "Authorization" not in result["request"]["headers"]
    assert "data" not in result["request"]
    assert "ana@example.com" not in result["exception"]["values"][0]["value"]
    assert "ana@example.com" not in result["logentry"]["message"]
