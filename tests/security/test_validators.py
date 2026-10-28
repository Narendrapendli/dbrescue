"""Security tests for input validators, regex rules, and allowlists."""

from __future__ import annotations

import pytest

from dbrescue.security import (
    ContainerNameDisallowedError,
    ForbiddenIdentifierError,
    SecurityValidationError,
    VolumeNameDisallowedError,
    sanitize_error_message,
    validate_backup_id,
    validate_container_name,
    validate_run_uuid,
    validate_sql_identifier,
    validate_volume_name,
)


@pytest.mark.security
def test_validate_sql_identifier_valid():
    """Asserts valid alphanumeric identifiers pass."""
    assert validate_sql_identifier("customers") == "customers"
    assert validate_sql_identifier("order_items_v2") == "order_items_v2"
    assert validate_sql_identifier("rescue_ecommerce") == "rescue_ecommerce"


@pytest.mark.security
def test_validate_sql_identifier_rejects_injection():
    """Asserts SQL injection characters and syntax are rejected."""
    malicious_inputs = [
        "customers; DROP TABLE users;--",
        "orders' OR '1'='1",
        "table`name",
        "order items",
        "schema--comment",
        "SELECT",
        "union",
        "drop",
    ]
    for bad_input in malicious_inputs:
        with pytest.raises(ForbiddenIdentifierError):
            validate_sql_identifier(bad_input)


@pytest.mark.security
def test_validate_run_uuid():
    """Asserts valid UUIDv4 strings pass and invalid strings are rejected."""
    valid_uuid = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
    assert validate_run_uuid(valid_uuid) == valid_uuid

    invalid_uuids = [
        "not-a-uuid",
        "../../../etc/passwd",
        "a1b2c3d4-e5f6-1a7b-8c9d-0e1f2a3b4c5d", # invalid version nibble (not 4)
        "",
    ]
    for bad in invalid_uuids:
        with pytest.raises(SecurityValidationError):
            validate_run_uuid(bad)


@pytest.mark.security
def test_validate_backup_id():
    """Asserts backup IDs pass and directory traversal attempts are blocked."""
    valid_id = "backup_20261028_223000_a1b2c3d4.sql.gz"
    assert validate_backup_id(valid_id) == valid_id

    traversal_attempts = [
        "../../etc/shadow",
        "..\\..\\Windows\\System32",
        "backup_20261028/../subfile.sql",
        "backup_invalid_format",
        "",
    ]
    for bad in traversal_attempts:
        with pytest.raises(SecurityValidationError):
            validate_backup_id(bad)


@pytest.mark.security
def test_validate_container_name_allowlist():
    """Asserts approved container names pass and unauthorized containers are blocked."""
    assert validate_container_name("dbrescue-mysql-primary") == "dbrescue-mysql-primary"
    assert validate_container_name("dbrescue-mysql-sandbox-test1") == "dbrescue-mysql-sandbox-test1"

    unauthorized_containers = [
        "my-unrelated-redis",
        "production-database",
        "nginx-proxy",
        "../../../bin/sh",
    ]
    for bad in unauthorized_containers:
        with pytest.raises(ContainerNameDisallowedError):
            validate_container_name(bad)


@pytest.mark.security
def test_validate_volume_name_allowlist():
    """Asserts approved volume names pass and unauthorized volumes are blocked."""
    assert validate_volume_name("dbrescue_mysql_data") == "dbrescue_mysql_data"
    assert validate_volume_name("dbrescue_test_vol") == "dbrescue_test_vol"

    unauthorized_volumes = [
        "host_root_filesystem",
        "docker_sock_vol",
        "production_live_volume",
    ]
    for bad in unauthorized_volumes:
        with pytest.raises(VolumeNameDisallowedError):
            validate_volume_name(bad)


@pytest.mark.security
def test_sanitize_error_message():
    """Asserts error message sanitizer scrubs passwords and URI tokens."""
    err = "Exception: failed to execute mysqldump -pSuperSecretPass! on host 127.0.0.1"
    sanitized = sanitize_error_message(err)
    assert "SuperSecretPass!" not in sanitized
    assert "-p***REDACTED***" in sanitized
