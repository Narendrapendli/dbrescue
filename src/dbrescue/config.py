"""Configuration management for the dbrescue platform.

Handles environment variable ingestion, typed Settings models, loopback port binding
validation, runtime bounds checks, sensitive value redaction, and operational settings
access via SQLite.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

# --- Core Enums ---

class Status(str, Enum):
    """Discrete operational status across all recovery, container, and audit workflows."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    DEGRADED = "DEGRADED"
    SKIPPED = "SKIPPED"


# --- Exceptions ---

class RescueException(Exception):
    """Base exception for all dbrescue runtime and validation errors."""
    def __init__(self, message: str, stage: str | None = None, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.details = details or {}


class ConfigError(RescueException):
    """Raised when configuration validation fails."""
    pass


class MissingEnvironmentVariableError(ConfigError):
    """Raised when a mandatory environment variable is absent."""
    pass


class InvalidPortBindingError(ConfigError):
    """Raised when a MySQL service is configured on a non-loopback interface."""
    pass


class InsecurePasswordError(ConfigError):
    """Raised when default placeholder passwords (e.g. CHANGE_ME) are detected."""
    pass


# --- Dataclasses ---

@dataclass(frozen=True)
class MySQLConnectionConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str
    root_password: str = field(repr=False)
    pool_size: int = 5
    connect_timeout_sec: int = 10

    def __repr__(self) -> str:
        return (
            f"MySQLConnectionConfig(host={self.host!r}, port={self.port}, "
            f"user={self.user!r}, database={self.database!r}, "
            f"password='***REDACTED***', root_password='***REDACTED***', "
            f"pool_size={self.pool_size}, connect_timeout_sec={self.connect_timeout_sec})"
        )

    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True)
class DockerConfig:
    primary_container_name: str
    sandbox_container_name: str
    target_volume_name: str
    mysql_image: str = "mysql:8.4.3"
    exporter_image: str = "prom/mysqld-exporter:v0.16.0"
    sandbox_host_port: int = 3307


@dataclass(frozen=True)
class OperationalBounds:
    backup_retention_days: int = 30
    max_dataset_size_mb: int = 10240
    sandbox_timeout_sec: int = 60
    max_allowed_row_discrepancy: int = 0


@dataclass(frozen=True)
class AppConfig:
    environment: str
    log_level: str
    sqlite_db_path: Path
    backup_dir: Path
    reports_dir: Path
    primary_mysql: MySQLConnectionConfig
    sandbox_mysql: MySQLConnectionConfig
    docker: DockerConfig
    bounds: OperationalBounds
    prometheus_metrics_port: int = 9108
    streamlit_server_port: int = 8501

    def __repr__(self) -> str:
        return (
            f"AppConfig(environment={self.environment!r}, log_level={self.log_level!r}, "
            f"sqlite_db_path={self.sqlite_db_path}, backup_dir={self.backup_dir}, "
            f"reports_dir={self.reports_dir}, primary_mysql={self.primary_mysql!r}, "
            f"sandbox_mysql={self.sandbox_mysql!r}, docker={self.docker!r}, "
            f"bounds={self.bounds!r}, prometheus_metrics_port={self.prometheus_metrics_port}, "
            f"streamlit_server_port={self.streamlit_server_port})"
        )

    def __str__(self) -> str:
        return self.__repr__()


# --- Validation Helpers ---

def _validate_port(port_val: int, param_name: str) -> None:
    if not (1 <= port_val <= 65535):
        raise ConfigError(f"Configuration parameter '{param_name}' must be between 1 and 65535. Got: {port_val}")


def _validate_loopback_host(host_val: str, param_name: str) -> None:
    loopbacks = {"127.0.0.1", "localhost", "::1"}
    if host_val.strip().lower() not in loopbacks:
        raise InvalidPortBindingError(
            f"Security Violation: '{param_name}' must bind strictly to a loopback address (127.0.0.1). "
            f"Got: '{host_val}'. Binding database listeners to 0.0.0.0 or public interfaces is forbidden."
        )


def _check_insecure_password(password_val: str, param_name: str) -> None:
    if "CHANGE_ME" in password_val.upper() or password_val.strip() in {"", "password", "root", "admin"}:
        raise InsecurePasswordError(
            f"Insecure Credential Error: '{param_name}' contains default placeholder or trivial password. "
            f"Update your .env file with a secure, non-default credential before running."
        )


def _get_required_env(var_name: str) -> str:
    val = os.getenv(var_name)
    if val is None or val.strip() == "":
        raise MissingEnvironmentVariableError(
            f"Mandatory environment variable '{var_name}' is not set. Check your .env file."
        )
    return val.strip()


# --- Loader Function ---

def load_config(env_file_path: Path | None = None) -> AppConfig:
    """Loads and validates configuration from environment variables and optional .env file.

    Raises:
        MissingEnvironmentVariableError: If mandatory environment variables are missing.
        InvalidPortBindingError: If MySQL hosts violate loopback isolation.
        InsecurePasswordError: If CHANGE_ME or trivial passwords are used.
        ConfigError: If numerical parameters, ports, or operational bounds are out of range.
    """
    if env_file_path and env_file_path.exists():
        load_dotenv(dotenv_path=env_file_path, override=True)
    else:
        # Default load
        load_dotenv(override=False)

    environment = os.getenv("DBRESCUE_ENV", "development").strip().lower()
    log_level = os.getenv("DBRESCUE_LOG_LEVEL", "INFO").strip().upper()

    sqlite_db_path = Path(os.getenv("DBRESCUE_SQLITE_PATH", "./db/sqlite/operational.db")).resolve()
    backup_dir = Path(os.getenv("DBRESCUE_BACKUP_DIR", "./backups")).resolve()
    reports_dir = Path(os.getenv("DBRESCUE_REPORTS_DIR", "./docs/reports")).resolve()

    # Ports
    try:
        primary_port = int(os.getenv("MYSQL_PRIMARY_PORT", "3306"))
        sandbox_port = int(os.getenv("MYSQL_SANDBOX_PORT", "3307"))
        prom_port = int(os.getenv("PROMETHEUS_METRICS_PORT", "9108"))
        st_port = int(os.getenv("STREAMLIT_SERVER_PORT", "8501"))
    except ValueError as e:
        raise ConfigError(f"Port values must be valid integers: {e}") from e

    _validate_port(primary_port, "MYSQL_PRIMARY_PORT")
    _validate_port(sandbox_port, "MYSQL_SANDBOX_PORT")
    _validate_port(prom_port, "PROMETHEUS_METRICS_PORT")
    _validate_port(st_port, "STREAMLIT_SERVER_PORT")

    # Hosts
    primary_host = os.getenv("MYSQL_PRIMARY_HOST", "127.0.0.1")
    sandbox_host = os.getenv("MYSQL_SANDBOX_HOST", "127.0.0.1")
    _validate_loopback_host(primary_host, "MYSQL_PRIMARY_HOST")
    _validate_loopback_host(sandbox_host, "MYSQL_SANDBOX_HOST")

    # Credentials
    db_name = os.getenv("MYSQL_DATABASE", "rescue_ecommerce").strip()
    db_user = os.getenv("MYSQL_USER", "rescue_user").strip()
    db_pass = _get_required_env("MYSQL_PASSWORD")
    root_pass = _get_required_env("MYSQL_ROOT_PASSWORD")

    _check_insecure_password(db_pass, "MYSQL_PASSWORD")
    _check_insecure_password(root_pass, "MYSQL_ROOT_PASSWORD")

    # Operational Bounds
    try:
        retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
        max_size_mb = int(os.getenv("MAX_DATASET_SIZE_MB", "10240"))
        sandbox_timeout = int(os.getenv("SANDBOX_TIMEOUT_SEC", "60"))
        max_discrepancy = int(os.getenv("MAX_ALLOWED_ROW_DISCREPANCY", "0"))
    except ValueError as e:
        raise ConfigError(f"Operational bounds parameters must be valid integers: {e}") from e

    if retention_days < 1:
        raise ConfigError(f"BACKUP_RETENTION_DAYS must be >= 1. Got: {retention_days}")
    if max_size_mb <= 0:
        raise ConfigError(f"MAX_DATASET_SIZE_MB must be > 0. Got: {max_size_mb}")
    if sandbox_timeout <= 0:
        raise ConfigError(f"SANDBOX_TIMEOUT_SEC must be > 0. Got: {sandbox_timeout}")
    if max_discrepancy < 0:
        raise ConfigError(f"MAX_ALLOWED_ROW_DISCREPANCY must be >= 0. Got: {max_discrepancy}")

    bounds = OperationalBounds(
        backup_retention_days=retention_days,
        max_dataset_size_mb=max_size_mb,
        sandbox_timeout_sec=sandbox_timeout,
        max_allowed_row_discrepancy=max_discrepancy,
    )

    primary_mysql = MySQLConnectionConfig(
        host=primary_host,
        port=primary_port,
        user=db_user,
        password=db_pass,
        database=db_name,
        root_password=root_pass,
    )

    sandbox_mysql = MySQLConnectionConfig(
        host=sandbox_host,
        port=sandbox_port,
        user=db_user,
        password=db_pass,
        database=db_name,
        root_password=root_pass,
    )

    docker_cfg = DockerConfig(
        primary_container_name=os.getenv("DOCKER_PRIMARY_CONTAINER", "dbrescue-mysql-primary"),
        sandbox_container_name=os.getenv("DOCKER_SANDBOX_CONTAINER", "dbrescue-mysql-sandbox"),
        target_volume_name=os.getenv("DOCKER_TARGET_VOLUME", "dbrescue_mysql_data"),
        sandbox_host_port=sandbox_port,
    )

    # Ensure local directory scaffolding
    backup_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        environment=environment,
        log_level=log_level,
        sqlite_db_path=sqlite_db_path,
        backup_dir=backup_dir,
        reports_dir=reports_dir,
        primary_mysql=primary_mysql,
        sandbox_mysql=sandbox_mysql,
        docker=docker_cfg,
        bounds=bounds,
        prometheus_metrics_port=prom_port,
        streamlit_server_port=st_port,
    )


# --- SQLite Operational Settings Access ---

def _init_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def get_operational_setting(key: str, default: str | None = None, sqlite_path: Path | None = None) -> str | None:
    """Retrieves dynamic runtime parameter from the SQLite 'settings' table."""
    path = sqlite_path or Path(os.getenv("DBRESCUE_SQLITE_PATH", "./db/sqlite/operational.db")).resolve()
    if not path.exists():
        return default

    try:
        with sqlite3.connect(path) as conn:
            _init_settings_table(conn)
            cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
    except sqlite3.Error:
        return default


def set_operational_setting(key: str, value: str, description: str | None = None, sqlite_path: Path | None = None) -> None:
    """Persists dynamic runtime parameter into the SQLite 'settings' table."""
    path = sqlite_path or Path(os.getenv("DBRESCUE_SQLITE_PATH", "./db/sqlite/operational.db")).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as conn:
        _init_settings_table(conn)
        conn.execute(
            """
            INSERT INTO settings (key, value, description, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                description = coalesce(excluded.description, settings.description),
                updated_at = CURRENT_TIMESTAMP;
            """,
            (key, value, description),
        )
        conn.commit()
