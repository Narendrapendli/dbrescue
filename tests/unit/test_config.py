"""Unit tests for dbrescue.config."""

from __future__ import annotations

import pytest

from dbrescue.config import (
    AppConfig,
    ConfigError,
    InsecurePasswordError,
    InvalidPortBindingError,
    MissingEnvironmentVariableError,
    get_operational_setting,
    load_config,
    set_operational_setting,
)


@pytest.fixture
def valid_env_vars(monkeypatch, tmp_path):
    """Provides a valid set of environment variables for testing."""
    env = {
        "DBRESCUE_ENV": "testing",
        "DBRESCUE_LOG_LEVEL": "DEBUG",
        "DBRESCUE_SQLITE_PATH": str(tmp_path / "test_operational.db"),
        "DBRESCUE_BACKUP_DIR": str(tmp_path / "backups"),
        "DBRESCUE_REPORTS_DIR": str(tmp_path / "reports"),
        "MYSQL_PRIMARY_HOST": "127.0.0.1",
        "MYSQL_PRIMARY_PORT": "3306",
        "MYSQL_SANDBOX_HOST": "127.0.0.1",
        "MYSQL_SANDBOX_PORT": "3307",
        "MYSQL_DATABASE": "test_db",
        "MYSQL_USER": "test_user",
        "MYSQL_PASSWORD": "ValidSecurePassword123!",
        "MYSQL_ROOT_PASSWORD": "ValidRootPassword456!",
        "DOCKER_PRIMARY_CONTAINER": "dbrescue-mysql-primary",
        "DOCKER_SANDBOX_CONTAINER": "dbrescue-mysql-sandbox",
        "DOCKER_TARGET_VOLUME": "dbrescue_mysql_data",
        "PROMETHEUS_METRICS_PORT": "9108",
        "STREAMLIT_SERVER_PORT": "8501",
        "BACKUP_RETENTION_DAYS": "14",
        "MAX_DATASET_SIZE_MB": "5120",
        "SANDBOX_TIMEOUT_SEC": "30",
        "MAX_ALLOWED_ROW_DISCREPANCY": "0",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


@pytest.mark.unit
def test_load_config_success(valid_env_vars):
    """Asserts valid configuration loads successfully with correct types."""
    cfg = load_config()
    assert isinstance(cfg, AppConfig)
    assert cfg.environment == "testing"
    assert cfg.primary_mysql.port == 3306
    assert cfg.sandbox_mysql.port == 3307
    assert cfg.primary_mysql.host == "127.0.0.1"
    assert cfg.bounds.backup_retention_days == 14
    assert cfg.bounds.sandbox_timeout_sec == 30


@pytest.mark.unit
def test_secrets_never_printed_in_repr(valid_env_vars):
    """Ensures password values are redacted in __repr__ and __str__."""
    cfg = load_config()
    repr_str = repr(cfg)
    str_val = str(cfg)
    conn_repr = repr(cfg.primary_mysql)

    # Actual secrets must not appear
    assert "ValidSecurePassword123!" not in repr_str
    assert "ValidRootPassword456!" not in repr_str
    assert "ValidSecurePassword123!" not in str_val
    assert "ValidSecurePassword123!" not in conn_repr

    # Redaction marker must be present
    assert "***REDACTED***" in repr_str
    assert "***REDACTED***" in conn_repr


@pytest.mark.unit
def test_refuse_change_me_passwords(monkeypatch, valid_env_vars):
    """Ensures default CHANGE_ME placeholder passwords are strictly rejected."""
    monkeypatch.setenv("MYSQL_PASSWORD", "CHANGE_ME_PRIMARY_PASSWORD")
    with pytest.raises(InsecurePasswordError, match="Insecure Credential Error"):
        load_config()

    monkeypatch.setenv("MYSQL_PASSWORD", "ValidSecurePassword123!")
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "CHANGE_ME_ROOT_PASSWORD")
    with pytest.raises(InsecurePasswordError, match="Insecure Credential Error"):
        load_config()


@pytest.mark.unit
def test_refuse_trivial_passwords(monkeypatch, valid_env_vars):
    """Ensures empty or trivial passwords (e.g. 'root') are rejected."""
    monkeypatch.setenv("MYSQL_PASSWORD", "root")
    with pytest.raises(InsecurePasswordError):
        load_config()


@pytest.mark.unit
def test_missing_mandatory_credential(monkeypatch, valid_env_vars):
    """Ensures absent password raises MissingEnvironmentVariableError."""
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)
    with pytest.raises(MissingEnvironmentVariableError, match="MYSQL_PASSWORD"):
        load_config()


@pytest.mark.unit
def test_reject_non_loopback_host(monkeypatch, valid_env_vars):
    """Ensures non-127.0.0.1 MySQL hosts are rejected for security."""
    monkeypatch.setenv("MYSQL_PRIMARY_HOST", "0.0.0.0")
    with pytest.raises(InvalidPortBindingError, match="must bind strictly to a loopback address"):
        load_config()

    monkeypatch.setenv("MYSQL_PRIMARY_HOST", "192.168.1.100")
    with pytest.raises(InvalidPortBindingError, match="must bind strictly to a loopback address"):
        load_config()


@pytest.mark.unit
def test_port_out_of_range(monkeypatch, valid_env_vars):
    """Ensures invalid port bounds raise ConfigError."""
    monkeypatch.setenv("MYSQL_PRIMARY_PORT", "70000")
    with pytest.raises(ConfigError, match="must be between 1 and 65535"):
        load_config()

    monkeypatch.setenv("MYSQL_PRIMARY_PORT", "-1")
    with pytest.raises(ConfigError, match="must be between 1 and 65535"):
        load_config()


@pytest.mark.unit
def test_operational_bounds_validation(monkeypatch, valid_env_vars):
    """Ensures invalid bounds raise ConfigError."""
    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "0")
    with pytest.raises(ConfigError, match="BACKUP_RETENTION_DAYS must be >= 1"):
        load_config()

    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "10")
    monkeypatch.setenv("SANDBOX_TIMEOUT_SEC", "-5")
    with pytest.raises(ConfigError, match="SANDBOX_TIMEOUT_SEC must be > 0"):
        load_config()


@pytest.mark.unit
def test_sqlite_operational_settings(tmp_path):
    """Tests dynamic operational key-value persistence in SQLite settings table."""
    db_file = tmp_path / "test_settings.db"
    assert get_operational_setting("non_existent_key", default="fallback", sqlite_path=db_file) == "fallback"

    set_operational_setting("maintenance_mode", "true", description="Emergency maintenance flag", sqlite_path=db_file)
    assert get_operational_setting("maintenance_mode", sqlite_path=db_file) == "true"

    # Upsert test
    set_operational_setting("maintenance_mode", "false", sqlite_path=db_file)
    assert get_operational_setting("maintenance_mode", sqlite_path=db_file) == "false"
