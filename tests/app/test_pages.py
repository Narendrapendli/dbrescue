"""Integration tests for remaining dashboard pages using Streamlit AppTest."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("MYSQL_PASSWORD", "test_secure_password_12345")
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "test_root_password_12345")
    monkeypatch.setenv("DBRESCUE_ENV", "testing")


def test_backup_restore_page_renders():
    """Verify Backup & Restore page renders inventory and actions cleanly."""
    with patch("app.services.get_backups") as mock_backups, \
         patch("app.services.get_retention_policy") as mock_policy:

        mock_backups.return_value = [
            {
                "id": 1,
                "filename": "backup_20261029.sql.gz",
                "file_size_bytes": 1048576,
                "sha256_checksum": "abcdef1234567890abcdef1234567890",
                "status": "VERIFIED_FILE",
                "created_at": "2026-10-29T00:00:00Z",
            }
        ]
        mock_policy.return_value = {"keep_last": 5, "max_age_days": 30}

        at = AppTest.from_file("app/main.py", default_timeout=10)
        at.run()
        at.sidebar.radio[0].set_value("💾 Backup & Restore")
        at.run()

        assert not at.exception
        assert len(at.title) > 0
        assert "Backup" in at.title[0].value


def test_live_health_page_renders():
    """Verify Live Health page displays telemetry gauges and alert evaluation without exception."""
    with patch("app.services.get_database_health") as mock_db, \
         patch("app.services.get_container_health") as mock_cntr, \
         patch("app.services.get_volume_health") as mock_vol, \
         patch("app.services.evaluate_alerts") as mock_eval:

        mock_db.return_value = {"reachable": True, "latency_ms": 1.5, "threads_connected": 3}
        mock_cntr.return_value = {"docker_available": True, "state": "running", "cpu_percent": 15.0, "memory_human": "120 MB"}
        mock_vol.return_value = {"exists": True, "mounted_correctly": True, "volume_name": "mysql_data"}
        mock_eval.return_value = []

        at = AppTest.from_file("app/main.py", default_timeout=10)
        at.run()
        at.sidebar.radio[0].set_value("🩺 Live Health")
        at.run()

        assert not at.exception
        assert len(at.title) > 0
        assert "Real-Time Telemetry" in at.title[0].value


def test_timeline_and_audit_logs_pages_render():
    """Verify Recovery Timeline and Audit Logs pages render event streams."""
    with patch("app.services.get_recent_audit_events") as mock_events, \
         patch("app.services.get_persistence_runs") as mock_runs:

        mock_events.return_value = [
            {
                "id": 1,
                "timestamp_utc": "2026-10-29T00:00:00Z",
                "operation": "STAGE_TRANSITION",
                "status": "INFO",
                "duration_ms": 10.0,
                "details": "Init DR",
            }
        ]
        mock_runs.return_value = []

        # 1. Timeline page
        at = AppTest.from_file("app/main.py", default_timeout=10)
        at.run()
        at.sidebar.radio[0].set_value("📜 Recovery Timeline")
        at.run()
        assert not at.exception
        assert "Timeline" in at.title[0].value

        # 2. Audit Logs page
        at.sidebar.radio[0].set_value("🔒 Audit Logs")
        at.run()
        assert not at.exception
        assert "Audit Trail" in at.title[0].value


def test_alerts_page_renders():
    """Verify Alerts page displays rules and firing alerts."""
    with patch("app.services.get_active_alerts") as mock_active, \
         patch("app.services.evaluate_alerts") as mock_evals:

        mock_active.return_value = []
        mock_evals.return_value = []

        at = AppTest.from_file("app/main.py", default_timeout=10)
        at.run()
        at.sidebar.radio[0].set_value("⚠️ Alerts")
        at.run()

        assert not at.exception
        assert len(at.title) > 0
        assert "Alerts" in at.title[0].value


def test_configuration_reports_page_renders():
    """Verify Configuration & Reports page displays tabs for settings, export, and PDF generation."""
    with patch("app.services.get_all_settings") as mock_settings, \
         patch("app.services.get_persistence_runs") as mock_runs:

        mock_settings.return_value = {"max_dataset_size_mb": 10240, "sandbox_timeout_sec": 60}
        mock_runs.return_value = []

        at = AppTest.from_file("app/main.py", default_timeout=10)
        at.run()
        at.sidebar.radio[0].set_value("⚙️ Configuration & Reports")
        at.run()

        assert not at.exception
        assert len(at.title) > 0
        assert "Configuration" in at.title[0].value
