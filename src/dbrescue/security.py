"""Security baseline, input validation, allowlists, and error message sanitization.

Defends against SQL injection, path traversal, container hijacking, and information
leakage across all dbrescue subsystems.
"""

from __future__ import annotations

import re

from dbrescue.config import RescueException

# --- Security Exceptions ---

class SecurityValidationError(RescueException):
    """Raised when user or system input fails strict security validation."""
    pass


class ForbiddenIdentifierError(SecurityValidationError):
    """Raised when an identifier violates strict alphanumeric naming rules."""
    pass


class ContainerNameDisallowedError(SecurityValidationError):
    """Raised when an unapproved container name is targeted."""
    pass


class VolumeNameDisallowedError(SecurityValidationError):
    """Raised when an unapproved volume name is targeted."""
    pass


# --- Strict Regex Patterns ---

# Standard SQL identifier: alphanumeric + underscore, max 64 chars (MySQL standard)
SQL_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z0-9_]{1,64}$")

# Run UUID (UUID v4)
RUN_UUID_REGEX = re.compile(r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$", re.IGNORECASE)

# Backup identifier: backup_YYYYMMDD_HHMMSS_hash or alphanumeric backup slug
BACKUP_ID_REGEX = re.compile(r"^backup_[0-9]{8}_[0-9]{6}_[a-f0-9]{8,16}(\.sql|\.sql\.gz)?$")

# Container / Volume name: alphanumeric, hyphens, underscores
DOCKER_NAME_REGEX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,127}$")


# --- Allowlists ---

ALLOWED_CONTAINER_NAMES: set[str] = {
    "dbrescue-mysql-primary",
    "dbrescue-mysql-sandbox",
    "dbrescue-inspector",
    "dbrescue-mysqld-exporter",
    "dbrescue-cadvisor",
    "dbrescue-prometheus",
    "dbrescue-grafana",
    "dbrescue-app",
}

ALLOWED_CONTAINER_PREFIXES: tuple[str, ...] = (
    "dbrescue-",
    "mysql-sandbox-",
)

ALLOWED_VOLUME_NAMES: set[str] = {
    "dbrescue_mysql_data",
    "dbrescue_mysql_sandbox_data",
    "dbrescue_prometheus_data",
    "dbrescue_grafana_data",
}

ALLOWED_VOLUME_PREFIXES: tuple[str, ...] = (
    "dbrescue_",
    "mysql_data_",
)


# --- Validators ---

def validate_sql_identifier(identifier: str, field_name: str = "identifier") -> str:
    """Validates an identifier (e.g. database, table, or column name) against strict pattern.

    Rejects spaces, semicolons, quotes, comment dashes, and non-alphanumeric chars.

    Raises:
        ForbiddenIdentifierError: If validation fails.
    """
    if not identifier or not isinstance(identifier, str):
        raise ForbiddenIdentifierError(f"Security Error: {field_name} must be a non-empty string.")

    cleaned = identifier.strip()
    if not SQL_IDENTIFIER_REGEX.match(cleaned):
        raise ForbiddenIdentifierError(
            f"Security Error: '{field_name}' contains illegal characters or exceeds 64 characters: '{cleaned}'. "
            f"Only alphanumeric characters and underscores are permitted."
        )

    # Reject known SQL injection fragments explicitly
    lowered = cleaned.lower()
    dangerous_keywords = {"union", "select", "drop", "alter", "delete", "truncate", "exec", "insert", "update"}
    if lowered in dangerous_keywords:
        raise ForbiddenIdentifierError(
            f"Security Error: '{field_name}' cannot be a reserved SQL keyword: '{cleaned}'."
        )

    return cleaned


def validate_run_uuid(run_uuid: str) -> str:
    """Validates that a run identifier is a valid UUIDv4 string.

    Raises:
        SecurityValidationError: If run_uuid is malformed.
    """
    if not run_uuid or not isinstance(run_uuid, str):
        raise SecurityValidationError("Security Error: run_uuid must be a non-empty string.")

    cleaned = run_uuid.strip()
    if not RUN_UUID_REGEX.match(cleaned):
        raise SecurityValidationError(f"Security Error: Invalid Run UUID format: '{cleaned}'.")

    return cleaned


def validate_backup_id(backup_id: str) -> str:
    """Validates backup filename or backup identifier string.

    Raises:
        SecurityValidationError: If backup_id contains directory traversal or invalid syntax.
    """
    if not backup_id or not isinstance(backup_id, str):
        raise SecurityValidationError("Security Error: backup_id must be a non-empty string.")

    cleaned = backup_id.strip()
    # Explicit directory traversal check
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise SecurityValidationError(f"Security Error: Directory traversal detected in backup_id: '{cleaned}'.")

    if not BACKUP_ID_REGEX.match(cleaned):
        raise SecurityValidationError(
            f"Security Error: backup_id '{cleaned}' does not match expected naming convention: "
            f"'backup_YYYYMMDD_HHMMSS_<hash>.sql.gz'."
        )

    return cleaned


def validate_container_name(container_name: str) -> str:
    """Checks container name against approved prefix allowlist and Docker naming rules.

    Raises:
        ContainerNameDisallowedError: If container name is not permitted.
    """
    if not container_name or not isinstance(container_name, str):
        raise ContainerNameDisallowedError("Container name must be a non-empty string.")

    cleaned = container_name.strip()
    if not DOCKER_NAME_REGEX.match(cleaned):
        raise ContainerNameDisallowedError(f"Container name '{cleaned}' violates Docker naming format.")

    if cleaned in ALLOWED_CONTAINER_NAMES:
        return cleaned

    if any(cleaned.startswith(prefix) for prefix in ALLOWED_CONTAINER_PREFIXES):
        return cleaned

    raise ContainerNameDisallowedError(
        f"Security Violation: Container '{cleaned}' is not in the dbrescue approved allowlist. "
        f"Allowed prefixes: {ALLOWED_CONTAINER_PREFIXES}"
    )


def validate_volume_name(volume_name: str) -> str:
    """Checks volume name against approved volume allowlist.

    Raises:
        VolumeNameDisallowedError: If volume name is not permitted.
    """
    if not volume_name or not isinstance(volume_name, str):
        raise VolumeNameDisallowedError("Volume name must be a non-empty string.")

    cleaned = volume_name.strip()
    if not DOCKER_NAME_REGEX.match(cleaned):
        raise VolumeNameDisallowedError(f"Volume name '{cleaned}' violates Docker naming format.")

    if cleaned in ALLOWED_VOLUME_NAMES:
        return cleaned

    if any(cleaned.startswith(prefix) for prefix in ALLOWED_VOLUME_PREFIXES):
        return cleaned

    raise VolumeNameDisallowedError(
        f"Security Violation: Volume '{cleaned}' is not in the dbrescue approved volume allowlist. "
        f"Allowed prefixes: {ALLOWED_VOLUME_PREFIXES}"
    )


def sanitize_error_message(error: Exception | str) -> str:
    """Sanitizes exception messages to prevent credential, password, or connection string leakage."""
    msg = str(error)

    # Scrub database URI credentials
    msg = re.sub(r"(?i)([a-z0-9+.-]+://[^:]+:)([^@]+)(@.+)", r"\1***REDACTED***\3", msg)

    # Scrub key=value passwords
    msg = re.sub(
        r"""(?i)(password|passwd|pwd|secret|token|api_?key|auth)\s*[:=]\s*['"]?([^'",\s}]+)['"]?""",
        r"\1=***REDACTED***",
        msg,
    )

    # Scrub potential root credentials in MySQL dump messages
    msg = re.sub(r"-p[^\s]+", "-p***REDACTED***", msg)

    return msg
