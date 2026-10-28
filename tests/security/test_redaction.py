"""Security tests for credential redaction filter and structured logging."""

from __future__ import annotations

import json
import logging

import pytest

from dbrescue.logging_setup import JSONFormatter, RedactionFilter


@pytest.mark.security
def test_redaction_filter_replaces_password_in_text():
    """Validates that plain text password substrings are scrubbed."""
    raw = "Connecting to MySQL using password='SuperSecretPassword123' at host 127.0.0.1"
    cleaned = RedactionFilter.redact_string(raw)
    assert "SuperSecretPassword123" not in cleaned
    assert "***REDACTED***" in cleaned


@pytest.mark.security
def test_redaction_filter_replaces_uri_credentials():
    """Validates that credentials embedded in database URIs are sanitized."""
    raw = "Target URI: mysql://root:SuperSecretPassword123@127.0.0.1:3306/rescue_ecommerce"
    cleaned = RedactionFilter.redact_string(raw)
    assert "SuperSecretPassword123" not in cleaned
    assert "mysql://root:***REDACTED***@127.0.0.1:3306/rescue_ecommerce" in cleaned


@pytest.mark.security
def test_redaction_filter_scrubs_dict_keys():
    """Validates that dictionary payloads scrub sensitive keys recursively."""
    payload = {
        "user": "rescue_user",
        "password": "ClearTextPassword!",
        "token": "bearer-xyz-12345",
        "nested": {
            "api_key": "secret-api-key-999",
            "safe_param": "safe_value"
        }
    }
    scrubbed = RedactionFilter._redact_dict(payload)
    assert scrubbed["user"] == "rescue_user"
    assert scrubbed["password"] == "***REDACTED***"
    assert scrubbed["token"] == "***REDACTED***"
    assert scrubbed["nested"]["api_key"] == "***REDACTED***"
    assert scrubbed["nested"]["safe_param"] == "safe_value"


@pytest.mark.security
def test_json_formatter_produces_valid_json():
    """Validates that JSONFormatter outputs well-formed JSON."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="dbrescue.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test event executed",
        args=(),
        exc_info=None
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "dbrescue.test"
    assert parsed["message"] == "Test event executed"
    assert "timestamp" in parsed
