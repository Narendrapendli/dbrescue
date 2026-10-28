"""Structured JSON logging with automated credential and sensitive data redaction.

Ensures that passwords, tokens, API keys, database connection URIs, and private strings
are never leaked to stdout, stderr, or log sinks.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Regular expressions for identifying sensitive patterns
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api_?key|auth|authorization|credential|root_password)"
)

# URI pattern: e.g. mysql://user:password@host:port/db
URI_PASSWORD_PATTERN = re.compile(
    r"(?i)([a-z0-9+.-]+://[^:]+:)([^@]+)(@.+)"
)

# Generic key-value pattern: password=xyz, password: xyz, "password": "xyz"
KEY_VALUE_SECRET_PATTERN = re.compile(
    r"""(?i)(["']?(?:password|passwd|pwd|secret|token|api_?key|auth|root_password)["']?\s*[:=]\s*["']?)([^"',\s}]+)(["']?)"""
)


class RedactionFilter(logging.Filter):
    """Logging filter that scrubs sensitive strings and credentials from log messages and attributes."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact_string(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact_value(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self.redact_string(str(a)) if isinstance(a, str) else a for a in record.args)

        # Redact any custom dict attributes attached to the log record
        for attr in ("details", "extra", "payload"):
            if hasattr(record, attr):
                val = getattr(record, attr)
                if isinstance(val, dict):
                    setattr(record, attr, self._redact_dict(val))

        return True

    @classmethod
    def redact_string(cls, text: str) -> str:
        """Redacts database URIs, key-value credentials, and sensitive tokens from text."""
        if not text:
            return text

        # Redact URIs
        scrubbed = URI_PASSWORD_PATTERN.sub(r"\1***REDACTED***\3", text)

        # Redact Key-Value pairs
        scrubbed = KEY_VALUE_SECRET_PATTERN.sub(r"\1***REDACTED***\3", scrubbed)

        return scrubbed

    @classmethod
    def _redact_value(cls, key: str, val: Any) -> Any:
        if SENSITIVE_KEY_PATTERN.search(str(key)):
            return "***REDACTED***"
        if isinstance(val, str):
            return cls.redact_string(val)
        if isinstance(val, dict):
            return cls._redact_dict(val)
        if isinstance(val, list):
            return [cls._redact_value(key, item) for item in val]
        return val

    @classmethod
    def _redact_dict(cls, data: dict[str, Any]) -> dict[str, Any]:
        redacted = {}
        for k, v in data.items():
            if SENSITIVE_KEY_PATTERN.search(str(k)):
                redacted[k] = "***REDACTED***"
            elif isinstance(v, dict):
                redacted[k] = cls._redact_dict(v)
            elif isinstance(v, list):
                redacted[k] = [cls._redact_value(k, item) for item in v]
            elif isinstance(v, str):
                redacted[k] = cls.redact_string(v)
            else:
                redacted[k] = v
        return redacted


class JSONFormatter(logging.Formatter):
    """Formats log records as structured, single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        # Include recovery stage if set
        if hasattr(record, "stage"):
            log_entry["stage"] = record.stage

        # Include custom details
        if hasattr(record, "details") and isinstance(record.details, dict):
            log_entry["details"] = record.details

        # Include exception info if available
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    json_format: bool = True
) -> logging.Logger:
    """Configures root logger with RedactionFilter and JSONFormatter.

    Args:
        level: Log level threshold (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional file path to append logs to.
        json_format: If True, outputs JSON; otherwise standard formatted text.
    """
    root_logger = logging.getLogger("dbrescue")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers.clear()

    redaction_filter = RedactionFilter()
    formatter = JSONFormatter() if json_format else logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.addFilter(redaction_filter)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.addFilter(redaction_filter)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    return root_logger
