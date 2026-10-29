"""Unit tests for alert state transitions, zero-evidence prevention, threshold rules, and auto-resolution."""

import datetime
from pathlib import Path

import pytest

from dbrescue.alerts import AlertManager, AlertSeverity, AlertState
from dbrescue.audit import AuditLogger
from dbrescue.monitoring import (
    ContainerMetrics,
    DatabaseMetrics,
    FreshnessStatus,
    TelemetrySnapshot,
    VolumeMetrics,
)


@pytest.fixture
def alert_env(tmp_path: Path) -> dict:
    sqlite_path = tmp_path / "test_alerts.sqlite3"
    audit_logger = AuditLogger(sqlite_path)

    manager = AlertManager(
        sqlite_db_path=sqlite_path,
        audit_logger=audit_logger,
        cpu_threshold_pct=85.0,
        memory_threshold_pct=85.0,
        max_backup_age_hours=24.0,
    )

    return {
        "manager": manager,
        "audit_logger": audit_logger,
    }


def test_alerts_initial_state_not_evaluated_without_evidence(alert_env: dict) -> None:
    """CRITICAL RULE: No alert is shown OK without evidence.

    Without telemetry or execution data, all rules must be NOT_EVALUATED.
    """
    manager: AlertManager = alert_env["manager"]

    evaluations = manager.evaluate_all(
        telemetry=None,
        last_run=None,
        newest_backup=None,
        last_restore=None,
    )

    assert len(evaluations) == 8
    for item in evaluations:
        assert (
            item.state == AlertState.NOT_EVALUATED
        ), f"Rule {item.rule_name} was prematurely evaluated!"
        assert (
            item.has_evidence is False
        ), f"Rule {item.rule_name} claimed evidence without execution!"


def test_alert_mysql_unavailable_firing_vs_ok(alert_env: dict) -> None:
    manager: AlertManager = alert_env["manager"]

    # 1. Unreachable -> FIRING
    telemetry_down = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(is_connected=False, latency_ms=None),
        volume=VolumeMetrics(),
    )
    evals = manager.evaluate_all(telemetry=telemetry_down)
    rule = next(e for e in evals if e.rule_name == "MYSQL_UNAVAILABLE")
    assert rule.state == AlertState.FIRING
    assert rule.severity == AlertSeverity.CRITICAL

    # 2. Reachable -> OK
    telemetry_up = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(is_connected=True, latency_ms=1.5),
        volume=VolumeMetrics(),
    )
    evals = manager.evaluate_all(telemetry=telemetry_up)
    rule = next(e for e in evals if e.rule_name == "MYSQL_UNAVAILABLE")
    assert rule.state == AlertState.OK
    assert rule.has_evidence is True


def test_alert_volume_unmounted_firing_vs_ok(alert_env: dict) -> None:
    manager: AlertManager = alert_env["manager"]

    # Unmounted
    telemetry_unmounted = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(exists=True, is_mounted=False),
    )
    evals = manager.evaluate_all(telemetry=telemetry_unmounted)
    rule = next(e for e in evals if e.rule_name == "VOLUME_UNMOUNTED")
    assert rule.state == AlertState.FIRING

    # Properly Mounted
    telemetry_mounted = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(exists=True, is_mounted=True, mountpoint="/var/lib/mysql"),
    )
    evals = manager.evaluate_all(telemetry=telemetry_mounted)
    rule = next(e for e in evals if e.rule_name == "VOLUME_UNMOUNTED")
    assert rule.state == AlertState.OK


def test_alert_record_count_mismatch_firing_vs_ok(alert_env: dict) -> None:
    manager: AlertManager = alert_env["manager"]

    # FAILED run -> FIRING
    failed_run = {
        "run_uuid": "11112222-3333-4444-5555-666677778888",
        "status": "FAILED",
        "error_message": "Parity error",
    }
    evals = manager.evaluate_all(last_run=failed_run)
    rule = next(e for e in evals if e.rule_name == "RECORD_COUNT_MISMATCH")
    assert rule.state == AlertState.FIRING

    # SUCCESS run -> OK
    success_run = {"run_uuid": "11112222-3333-4444-5555-666677778888", "status": "SUCCESS"}
    evals = manager.evaluate_all(last_run=success_run)
    rule = next(e for e in evals if e.rule_name == "RECORD_COUNT_MISMATCH")
    assert rule.state == AlertState.OK


def test_alert_backup_overdue_firing_vs_ok(alert_env: dict) -> None:
    manager: AlertManager = alert_env["manager"]

    # 30 hours old -> FIRING (exceeds 24h threshold)
    old_time = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=30)).isoformat()
    old_backup = {"created_at": old_time, "filename": "old_backup.sql.gz"}
    evals = manager.evaluate_all(newest_backup=old_backup)
    rule = next(e for e in evals if e.rule_name == "BACKUP_OVERDUE")
    assert rule.state == AlertState.FIRING

    # 2 hours old -> OK
    recent_time = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2)).isoformat()
    recent_backup = {"created_at": recent_time, "filename": "recent_backup.sql.gz"}
    evals = manager.evaluate_all(newest_backup=recent_backup)
    rule = next(e for e in evals if e.rule_name == "BACKUP_OVERDUE")
    assert rule.state == AlertState.OK


def test_alert_metrics_stale_firing_vs_ok(alert_env: dict) -> None:
    manager: AlertManager = alert_env["manager"]

    # STALE -> FIRING
    telemetry_stale = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(),
        freshness=FreshnessStatus.STALE,
        age_seconds=120.0,
    )
    evals = manager.evaluate_all(telemetry=telemetry_stale)
    rule = next(e for e in evals if e.rule_name == "METRICS_STALE")
    assert rule.state == AlertState.FIRING

    # FRESH -> OK
    telemetry_fresh = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(),
        freshness=FreshnessStatus.FRESH,
        age_seconds=2.0,
    )
    evals = manager.evaluate_all(telemetry=telemetry_fresh)
    rule = next(e for e in evals if e.rule_name == "METRICS_STALE")
    assert rule.state == AlertState.OK


def test_alert_cpu_memory_thresholds_unknown_vs_zero(alert_env: dict) -> None:
    manager: AlertManager = alert_env["manager"]

    # UNKNOWN (None) -> NOT_EVALUATED (Never false OK or false FIRING)
    telemetry_unknown = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(cpu_percent=None, memory_percent=None),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(),
    )
    evals = manager.evaluate_all(telemetry=telemetry_unknown)
    cpu_rule = next(e for e in evals if e.rule_name == "CPU_HIGH_THRESHOLD")
    mem_rule = next(e for e in evals if e.rule_name == "MEMORY_HIGH_THRESHOLD")
    assert cpu_rule.state == AlertState.NOT_EVALUATED
    assert mem_rule.state == AlertState.NOT_EVALUATED

    # High CPU (92.5% > 85.0%) -> FIRING
    telemetry_high = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(cpu_percent=92.5, memory_percent=45.0),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(),
    )
    evals = manager.evaluate_all(telemetry=telemetry_high)
    cpu_rule = next(e for e in evals if e.rule_name == "CPU_HIGH_THRESHOLD")
    mem_rule = next(e for e in evals if e.rule_name == "MEMORY_HIGH_THRESHOLD")
    assert cpu_rule.state == AlertState.FIRING
    assert mem_rule.state == AlertState.OK

    # Zero CPU (0.0% - measured actual idle) -> OK
    telemetry_zero = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(cpu_percent=0.0, memory_percent=10.0),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(),
    )
    evals = manager.evaluate_all(telemetry=telemetry_zero)
    cpu_rule = next(e for e in evals if e.rule_name == "CPU_HIGH_THRESHOLD")
    assert cpu_rule.state == AlertState.OK
    assert cpu_rule.details["cpu_percent"] == 0.0


def test_alert_auto_resolve_in_sqlite(alert_env: dict) -> None:
    manager: AlertManager = alert_env["manager"]

    # 1. Trigger firing alert for database down
    telemetry_down = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=100.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(is_connected=False),
        volume=VolumeMetrics(),
    )
    manager.evaluate_all(telemetry=telemetry_down)

    active_alerts = manager.list_active_alerts()
    assert any(a.alert_name == "MYSQL_UNAVAILABLE" for a in active_alerts)

    # 2. Database recovers -> alert automatically resolved
    telemetry_up = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:01:00Z",
        timestamp_mono=160.0,
        container=ContainerMetrics(),
        database=DatabaseMetrics(is_connected=True, latency_ms=2.0),
        volume=VolumeMetrics(),
    )
    manager.evaluate_all(telemetry=telemetry_up)

    active_alerts_after = manager.list_active_alerts()
    assert not any(a.alert_name == "MYSQL_UNAVAILABLE" for a in active_alerts_after)
