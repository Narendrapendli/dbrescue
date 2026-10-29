"""Unit tests for telemetry collection, freshness auditing, and UNKNOWN vs zero distinctions."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.config import AppConfig, MySQLConnectionConfig
from dbrescue.docker_manager import ContainerInfo, DockerManager
from dbrescue.monitoring import (
    ContainerMetrics,
    DatabaseMetrics,
    FreshnessStatus,
    MonitoringCollector,
    TelemetrySnapshot,
    VolumeMetrics,
)
from dbrescue.volume_inspector import VolumeInfo, VolumeInspector


@pytest.fixture
def monitoring_env(tmp_path: Path) -> dict:
    sqlite_path = tmp_path / "test_mon_audit.sqlite3"
    audit_logger = AuditLogger(sqlite_path)

    cfg = MagicMock(spec=AppConfig)
    cfg.sqlite_db_path = sqlite_path
    cfg.mysql = MySQLConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="test_user",
        password="TestPassword2026!",
        database="student_db",
        root_password="RootPassword2026!",
    )
    cfg.docker = MagicMock()
    cfg.docker.primary_container_name = "mysql_prod"
    cfg.docker.volume_name = "mysql_data"

    docker_mgr = MagicMock(spec=DockerManager)
    vol_inspector = MagicMock(spec=VolumeInspector)

    collector = MonitoringCollector(
        sqlite_db_path=sqlite_path,
        config=cfg,
        docker_manager=docker_mgr,
        volume_inspector=vol_inspector,
        freshness_threshold_sec=30.0,
        audit_logger=audit_logger,
    )

    return {
        "collector": collector,
        "config": cfg,
        "docker_manager": docker_mgr,
        "volume_inspector": vol_inspector,
        "audit_logger": audit_logger,
        "sqlite_path": sqlite_path,
    }


def test_measure_db_latency_success(monitoring_env: dict) -> None:
    collector: MonitoringCollector = monitoring_env["collector"]
    db_cfg: MySQLConnectionConfig = monitoring_env["config"].mysql

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # SELECT 1, then SHOW STATUS
    mock_cursor.fetchone.return_value = ("Threads_connected", "5")

    with patch("mysql.connector.connect", return_value=mock_conn):
        connected, latency, threads = collector.measure_db_latency(db_cfg)

    assert connected is True
    assert latency is not None
    assert latency >= 0.0
    assert threads == 5


def test_measure_db_latency_unreachable_unknown_not_zero(monitoring_env: dict) -> None:
    collector: MonitoringCollector = monitoring_env["collector"]
    db_cfg: MySQLConnectionConfig = monitoring_env["config"].mysql

    # When database is unreachable, latency must be None (UNKNOWN), never false 0.0!
    with patch("mysql.connector.connect", side_effect=Exception("Connection refused")):
        connected, latency, threads = collector.measure_db_latency(db_cfg)

    assert connected is False
    assert latency is None  # UNKNOWN, not 0.0
    assert threads is None


def test_collect_container_stats_running_with_stats(monitoring_env: dict) -> None:
    collector: MonitoringCollector = monitoring_env["collector"]
    docker_mgr: MagicMock = monitoring_env["docker_manager"]

    docker_mgr.get_container_state.return_value = ContainerInfo(
        container_id="cont123",
        name="mysql_prod",
        status="running",
        image="mysql:8.4.3",
        created_at="2026-10-29T00:00:00Z",
    )

    mock_client = MagicMock()
    mock_container = MagicMock()
    docker_mgr._get_client.return_value = mock_client
    mock_client.containers.get.return_value = mock_container

    mock_container.stats.return_value = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 500000000},
            "system_cpu_usage": 1000000000,
            "online_cpus": 2,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 400000000},
            "system_cpu_usage": 900000000,
        },
        "memory_stats": {
            "usage": 209715200,  # 200 MB
            "limit": 1073741824,  # 1 GB
        },
    }

    metrics = collector.collect_container_stats("mysql_prod")
    assert metrics.state == "running"
    assert metrics.is_running is True
    assert metrics.cpu_percent is not None
    assert metrics.cpu_percent == 200.0
    assert metrics.memory_usage_bytes == 209715200
    assert metrics.memory_percent == 19.53


def test_collect_container_stats_unknown_when_unavailable(monitoring_env: dict) -> None:
    collector: MonitoringCollector = monitoring_env["collector"]
    docker_mgr: MagicMock = monitoring_env["docker_manager"]

    # When container is exited or stats call fails, values must be None (UNKNOWN, not 0.0)
    docker_mgr.get_container_state.return_value = ContainerInfo(
        container_id="cont123",
        name="mysql_prod",
        status="exited",
        image="mysql:8.4.3",
        created_at="2026-10-29T00:00:00Z",
    )

    metrics = collector.collect_container_stats("mysql_prod")
    assert metrics.state == "exited"
    assert metrics.is_running is False
    assert metrics.cpu_percent is None
    assert metrics.memory_percent is None


def test_collect_volume_status(monitoring_env: dict) -> None:
    collector: MonitoringCollector = monitoring_env["collector"]
    vol_inspector: MagicMock = monitoring_env["volume_inspector"]

    vol_inspector.inspect_volume.return_value = VolumeInfo(
        name="mysql_data",
        driver="local",
        mountpoint="/var/lib/docker/volumes/mysql_data/_data",
        size_display="15.2 MB",
    )
    mount_mock = MagicMock()
    mount_mock.source = "mysql_data"
    vol_inspector.inspect_container_mounts.return_value = [mount_mock]

    vol_metrics = collector.collect_volume_status("mysql_data", "mysql_prod")
    assert vol_metrics.exists is True
    assert vol_metrics.is_mounted is True
    assert vol_metrics.size_display == "15.2 MB"


def test_freshness_evaluation_fresh_vs_stale(monitoring_env: dict) -> None:
    collector: MonitoringCollector = monitoring_env["collector"]

    # Initial snapshot is fresh
    snap = collector.collect_telemetry()
    assert snap.freshness == FreshnessStatus.FRESH

    # Query immediately -> fresh
    telemetry_now = collector.get_telemetry()
    assert telemetry_now.freshness == FreshnessStatus.FRESH

    # Fast forward sample age beyond freshness_threshold_sec (30.0s)
    old_mono = time.perf_counter() - 45.0
    collector._last_snapshot = TelemetrySnapshot(
        timestamp_utc=snap.timestamp_utc,
        timestamp_mono=old_mono,
        container=ContainerMetrics(),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(),
        freshness=FreshnessStatus.FRESH,
    )

    telemetry_stale = collector.get_telemetry()
    assert telemetry_stale.freshness == FreshnessStatus.STALE
    assert telemetry_stale.age_seconds >= 45.0


def test_persist_metrics_samples_to_sqlite(monitoring_env: dict) -> None:
    collector: MonitoringCollector = monitoring_env["collector"]
    audit_logger: AuditLogger = monitoring_env["audit_logger"]

    # Mock latency and stats
    with (
        patch.object(collector, "measure_db_latency", return_value=(True, 2.5, 4)),
        patch.object(
            collector,
            "collect_container_stats",
            return_value=ContainerMetrics(
                state="running", cpu_percent=12.5, memory_usage_bytes=104857600
            ),
        ),
    ):
        collector.collect_telemetry()

    conn = audit_logger._get_connection()
    try:
        rows = conn.execute("SELECT metric_name, metric_value FROM metrics_samples;").fetchall()
        names = {r["metric_name"]: r["metric_value"] for r in rows}
        assert "db_query_latency_ms" in names
        assert names["db_query_latency_ms"] == 2.5
        assert "db_threads_connected" in names
        assert names["db_threads_connected"] == 4.0
        assert "container_cpu_percent" in names
        assert names["container_cpu_percent"] == 12.5
    finally:
        conn.close()


def test_metrics_exporter_update_from_state(monitoring_env: dict) -> None:
    import uuid

    from dbrescue.metrics_exporter import (
        GAUGE_ACTIVE_ALERTS,
        GAUGE_LAST_TEST_STATUS,
        update_metrics_from_state,
    )

    sqlite_path = monitoring_env["sqlite_path"]
    audit_logger = monitoring_env["audit_logger"]

    # Seed run and alert in SQLite
    run_id = audit_logger.create_run(
        run_uuid=str(uuid.uuid4()),
        target_volume="mysql_data",
        primary_container="mysql_prod",
    )
    audit_logger.complete_run(run_id=run_id, status="SUCCESS", final_stage="FINALIZE_RUN")
    audit_logger.record_alert("TEST_ALERT", "WARNING", "Test warning alert")

    values = update_metrics_from_state(sqlite_path)
    assert values["dbrescue_last_test_status"] == 1.0
    assert values["dbrescue_active_alerts_count"] >= 1.0
    assert GAUGE_LAST_TEST_STATUS._value.get() == 1.0
    assert GAUGE_ACTIVE_ALERTS._value.get() >= 1.0
