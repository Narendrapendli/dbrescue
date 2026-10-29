"""AppTest integration tests for Streamlit dashboard.

Ensures pages render without uncaught exceptions and empty states render gracefully.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Provide valid environment variables so config does not raise MissingEnvironmentVariableError."""
    monkeypatch.setenv("MYSQL_PASSWORD", "test_secure_password_12345")
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "test_root_password_12345")
    monkeypatch.setenv("DBRESCUE_ENV", "testing")


def test_app_main_renders_without_exception(tmp_path):
    """Verify that main dashboard launches and renders the overview page cleanly."""
    with (
        patch("app.services.get_database_health") as mock_db,
        patch("app.services.get_container_health") as mock_cntr,
        patch("app.services.get_volume_health") as mock_vol,
        patch("app.services.get_latest_persistence_run") as mock_run,
        patch("app.services.get_latest_backup") as mock_bup,
        patch("app.services.get_active_alerts") as mock_alerts,
        patch("app.services.get_recent_audit_events") as mock_events,
        patch("app.services.get_reliability_scorecard") as mock_sc,
    ):
        mock_db.return_value = {
            "reachable": True,
            "latency_ms": 1.2,
            "uptime_seconds": 120,
            "version": "8.4.3",
        }
        mock_cntr.return_value = {"state": "running", "healthy": True, "health_status": "healthy"}
        mock_vol.return_value = {
            "exists": True,
            "mounted_correctly": True,
            "volume_name": "mysql_data",
            "size_human": "50 MB",
        }
        mock_run.return_value = None
        mock_bup.return_value = None
        mock_alerts.return_value = []
        mock_events.return_value = []
        mock_sc.return_value = {"tested": False}

        at = AppTest.from_file("app/main.py", default_timeout=10)
        at.run()

        assert not at.exception
        assert len(at.title) > 0
        assert "System Overview & Disaster Recovery Status" in at.title[0].value


def test_app_persistence_tests_page_renders():
    """Verify navigation to Persistence Tests page renders the safety confirmation."""
    with (
        patch("app.services.get_database_health") as mock_db,
        patch("app.services.get_container_health") as mock_cntr,
        patch("app.services.get_volume_health") as mock_vol,
        patch("app.services.get_persistence_runs") as mock_runs,
    ):
        mock_db.return_value = {"reachable": False}
        mock_cntr.return_value = {"state": "exited"}
        mock_vol.return_value = {"exists": True, "mounted_correctly": True}
        mock_runs.return_value = []

        at = AppTest.from_file("app/main.py", default_timeout=10)
        at.run()

        # Select the 'Persistence Tests' radio item
        at.sidebar.radio[0].set_value("🚀 Persistence Tests")
        at.run()

        assert not at.exception
        assert len(at.title) > 0
        assert "Disaster Recovery" in at.title[0].value
