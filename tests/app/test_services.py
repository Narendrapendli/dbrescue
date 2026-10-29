"""Tests for app.services module.

Validates that app.services interfaces with backend modules safely,
enforces safety tokens, and avoids fabricated data.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import (
    execute_allowlisted_query,
    execute_persistence_test,
    get_allowlisted_queries,
    get_reliability_scorecard,
)
from dbrescue.config import Status


def test_get_reliability_scorecard_empty(tmp_path):
    """Reliability scorecard must report tested=False and zero fake numbers when no runs exist."""
    with patch("app.services.get_audit_store") as mock_store_getter:
        mock_store = MagicMock()
        mock_store.list_runs.return_value = []
        mock_store_getter.return_value = mock_store

        scorecard = get_reliability_scorecard()
        assert scorecard["tested"] is False
        assert scorecard["total_runs"] == 0
        assert scorecard["success_rate_percent"] is None
        assert scorecard["avg_rto_seconds"] is None


def test_get_reliability_scorecard_populated():
    """Reliability scorecard calculates empirical RTO and success rate from real runs."""
    with patch("app.services.get_audit_store") as mock_store_getter:
        mock_store = MagicMock()
        mock_store.list_runs.return_value = [
            {"id": 1, "status": Status.SUCCESS.value, "duration_seconds": 12.5},
            {"id": 2, "status": Status.SUCCESS.value, "duration_seconds": 17.5},
            {"id": 3, "status": Status.FAILED.value, "duration_seconds": 8.0},
        ]
        mock_store_getter.return_value = mock_store

        scorecard = get_reliability_scorecard()
        assert scorecard["tested"] is True
        assert scorecard["total_runs"] == 3
        assert scorecard["successful_runs"] == 2
        assert scorecard["success_rate_percent"] == 66.7
        assert scorecard["avg_rto_seconds"] == 15.0
        assert scorecard["min_rto_seconds"] == 12.5
        assert scorecard["max_rto_seconds"] == 17.5
        assert scorecard["rpo_seconds"] == 0.0


def test_execute_persistence_test_token_guard():
    """execute_persistence_test raises ValueError if typed container name does not match config."""
    with patch("app.services.get_config") as mock_cfg_getter:
        mock_cfg = MagicMock()
        mock_cfg.docker.primary_container_name = "mysql_prod"
        mock_cfg_getter.return_value = mock_cfg

        with pytest.raises(ValueError, match="Container name confirmation mismatch"):
            execute_persistence_test(confirm_container_name="wrong_container")


def test_allowlisted_query_rejection():
    """execute_allowlisted_query raises ValueError when given an arbitrary SQL statement."""
    with pytest.raises(ValueError, match="not in the read-only allowlist"):
        execute_allowlisted_query("DROP TABLE students;")


def test_allowlisted_queries_dictionary():
    """Allowlist dictionary must only contain SELECT/SHOW queries."""
    queries = get_allowlisted_queries()
    assert len(queries) > 0
    for _title, sql in queries.items():
        assert sql.strip().upper().startswith(("SELECT", "SHOW"))
