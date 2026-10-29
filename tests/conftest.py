"""Global pytest configuration, shared fixtures, and destructive safety guards.

SAFETY GUARD:
Aborts immediately if any destructive operation targets production volumes ('mysql_data')
or non-disposable environments. Disposable integration tests MUST target 'dbrescue_ci'
with volume 'dbrescue_ci_data'.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ and root are on sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


FORBIDDEN_DESTRUCTIVE_VOLUMES = {"mysql_data", "dbrescue_mysql_data_prod"}
DISPOSABLE_CI_PROJECT = "dbrescue_ci"
DISPOSABLE_CI_VOLUME = "dbrescue_ci_data"


@pytest.fixture(autouse=True)
def base_test_environment(monkeypatch):
    """Enforce safe test defaults across all test runs."""
    monkeypatch.setenv("MYSQL_PASSWORD", "test_secure_password_12345")
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "test_root_password_12345")
    monkeypatch.setenv("DBRESCUE_ENV", "testing")


@pytest.fixture
def safety_guard():
    """Safety guard fixture that prevents destructive tests against production assets."""
    def _assert_safe_target(volume_name: str, container_name: str) -> None:
        if volume_name in FORBIDDEN_DESTRUCTIVE_VOLUMES:
            pytest.fail(
                f"SAFETY INTERCEPT: Destructive test attempted against protected volume '{volume_name}'. "
                f"Disposable CI project must target '{DISPOSABLE_CI_VOLUME}' only."
            )
        if "prod" in container_name.lower() and "ci" not in container_name.lower():
            pytest.fail(
                f"SAFETY INTERCEPT: Destructive test attempted against production container '{container_name}'."
            )

    return _assert_safe_target


@pytest.fixture
def disposable_ci_config(tmp_path):
    """Provides an isolated AppConfig specifically bound to the disposable dbrescue_ci stack."""
    from dbrescue.config import AppConfig, DockerConfig, MySQLConnectionConfig, OperationalBounds

    sqlite_path = tmp_path / "ci_audit.db"
    backup_path = tmp_path / "ci_backups"
    reports_path = tmp_path / "ci_reports"
    backup_path.mkdir(parents=True, exist_ok=True)
    reports_path.mkdir(parents=True, exist_ok=True)

    primary_mysql = MySQLConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="rescue_user",
        password="test_secure_password_12345",
        database="dbrescue_ci_test",
        root_password="test_root_password_12345",
    )

    sandbox_mysql = MySQLConnectionConfig(
        host="127.0.0.1",
        port=3307,
        user="rescue_user",
        password="test_secure_password_12345",
        database="dbrescue_ci_test",
        root_password="test_root_password_12345",
    )

    docker_cfg = DockerConfig(
        primary_container_name="dbrescue_ci_mysql",
        sandbox_container_name="dbrescue_ci_mysql_sandbox",
        target_volume_name=DISPOSABLE_CI_VOLUME,
        sandbox_host_port=3307,
    )

    bounds = OperationalBounds(
        backup_retention_days=7,
        max_dataset_size_mb=1024,
        sandbox_timeout_sec=30,
        max_allowed_row_discrepancy=0,
    )

    return AppConfig(
        environment="ci",
        log_level="DEBUG",
        sqlite_db_path=sqlite_path,
        backup_dir=backup_path,
        reports_dir=reports_path,
        primary_mysql=primary_mysql,
        sandbox_mysql=sandbox_mysql,
        docker=docker_cfg,
        bounds=bounds,
        prometheus_metrics_port=9108,
        streamlit_server_port=8501,
    )
