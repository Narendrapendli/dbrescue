"""Comprehensive failure injection and error handling test suite for dbrescue.

Validates that every operational error condition is reliably detected, typed
exceptions are raised, and audit/alert states capture failure evidence.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from dbrescue.alerts import AlertManager, AlertState
from dbrescue.audit import AuditLogger
from dbrescue.backup import (
    BackupChecksumMismatchError,
    BackupManager,
    DumpProcessFailedError,
    compute_file_sha256,
)
from dbrescue.config import MySQLConnectionConfig, Status
from dbrescue.db import DatabaseUnavailable, create_connection_pool
from dbrescue.docker_manager import (
    ContainerInfo,
    DockerManager,
    DockerUnavailable,
    ReadinessTimeout,
    VolumeMisMounted,
    VolumeMissing,
)
from dbrescue.integrity import compare_datasets
from dbrescue.monitoring import (
    ContainerMetrics,
    DatabaseMetrics,
    FreshnessStatus,
    MonitoringCollector,
    TelemetrySnapshot,
    VolumeMetrics,
)
from dbrescue.recovery import RecoveryOrchestrator, RecoveryStage
from dbrescue.restore import CorruptBackupArchiveError, RestoreEngine
from dbrescue.volume_inspector import VolumeInspector


@pytest.mark.errors
def test_docker_unavailable_handling(disposable_ci_config):
    """When Docker daemon is unreachable, is_docker_available returns False and orchestrator aborts."""
    with patch("docker.from_env", side_effect=Exception("Docker daemon socket closed")):
        dm = DockerManager(docker_config=disposable_ci_config.docker)
        assert dm.is_docker_available() is False

        with pytest.raises(DockerUnavailable, match="Docker daemon is not available or unreachable"):
            dm.get_container_state("dbrescue-mysql-primary")

        orchestrator = RecoveryOrchestrator(
            config=disposable_ci_config,
            docker_manager=dm,
        )
        res = orchestrator.execute_recovery(
            target_volume=disposable_ci_config.docker.target_volume_name,
            confirm_destructive=True,
            failure_injection="DOCKER_DOWN",
        )
        assert res.passed is False
        assert res.status == Status.FAILED
        assert res.final_stage == RecoveryStage.FINALIZE_RUN


@pytest.mark.errors
def test_mysql_unavailable_handling(disposable_ci_config):
    """When MySQL is unreachable, DatabaseUnavailable is raised and alert engine triggers FIRING."""
    # 1. Connection pool raises DatabaseUnavailable when unreachable
    unreachable_cfg = MySQLConnectionConfig(
        host="127.0.0.1",
        port=59999,
        user="nobody",
        password="ValidPassword123!",
        database="none",
        root_password="RootPassword123!",
        connect_timeout_sec=1,
    )
    with pytest.raises(DatabaseUnavailable):
        create_connection_pool(unreachable_cfg, max_retries=1, backoff_base_sec=0.01)

    # 2. Telemetry collector handles unreachable DB without raising
    collector = MonitoringCollector(sqlite_db_path=disposable_ci_config.sqlite_db_path)
    is_conn, lat, threads = collector.measure_db_latency(unreachable_cfg)
    assert is_conn is False
    assert lat is None
    assert threads is None

    # 3. Alert manager evaluates MYSQL_UNAVAILABLE as FIRING
    alert_mgr = AlertManager(sqlite_db_path=disposable_ci_config.sqlite_db_path)
    snapshot = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=time.perf_counter(),
        container=ContainerMetrics(state="running"),
        database=DatabaseMetrics(is_connected=False, latency_ms=None),
        volume=VolumeMetrics(exists=True, is_mounted=True),
    )
    evals = alert_mgr.evaluate_all(telemetry=snapshot)
    db_alert = next(e for e in evals if e.rule_name == "MYSQL_UNAVAILABLE")
    assert db_alert.state == AlertState.FIRING


@pytest.mark.errors
def test_missing_volume_handling(disposable_ci_config):
    """Missing volume raises VolumeMissing and fails recovery orchestration."""
    inspector = VolumeInspector()
    with patch.object(inspector, "_get_client") as mock_client:
        from docker.errors import NotFound
        mock_client.return_value.volumes.get.side_effect = NotFound("Volume not found")

        with pytest.raises(VolumeMissing, match="does not exist"):
            inspector.inspect_volume("dbrescue_missing_vol")

    orchestrator = RecoveryOrchestrator(config=disposable_ci_config)
    res = orchestrator.execute_recovery(
        target_volume="dbrescue_ci_data",
        confirm_destructive=True,
        failure_injection="VOLUME_MISSING",
    )
    assert res.passed is False
    assert res.status == Status.FAILED
    assert res.final_stage == RecoveryStage.FINALIZE_RUN


@pytest.mark.errors
def test_wrong_mount_bind_and_anonymous_violation():
    """VolumeMisMounted is raised when host bind mount or anonymous volume is detected."""
    inspector = VolumeInspector()

    # Bind mount violation
    with patch.object(inspector, "_get_client") as mock_client:
        mock_container = MagicMock()
        mock_container.attrs = {
            "Mounts": [{"Destination": "/var/lib/mysql", "Type": "bind", "Source": "C:\\host\\data"}]
        }
        mock_client.return_value.containers.get.return_value = mock_container

        with pytest.raises(VolumeMisMounted, match="host bind mount"):
            inspector.inspect_container_mounts(
                container_name_or_id="dbrescue-mysql-primary",
                expected_volume_name="dbrescue_ci_data",
            )

    # Anonymous/unauthorized volume violation
    with patch.object(inspector, "_get_client") as mock_client:
        mock_container = MagicMock()
        mock_container.attrs = {
            "Mounts": [{"Destination": "/var/lib/mysql", "Type": "volume", "Name": "unapproved_volume"}]
        }
        mock_client.return_value.containers.get.return_value = mock_container

        with pytest.raises(VolumeMisMounted, match="unauthorized or anonymous volume"):
            inspector.inspect_container_mounts(
                container_name_or_id="dbrescue-mysql-primary",
                expected_volume_name="dbrescue_ci_data",
            )


@pytest.mark.errors
def test_readiness_timeout_handling(disposable_ci_config):
    """ReadinessTimeout is raised when container does not become healthy within timeout."""
    dm = DockerManager(docker_config=disposable_ci_config.docker)
    with patch.object(dm, "get_container_state") as mock_state:
        mock_state.return_value = ContainerInfo(
            container_id="123",
            name="dbrescue-mysql-primary",
            status="running",
            image="mysql:8.4.3",
            created_at="2026-10-29T00:00:00Z",
            health_status="starting",
        )
        with pytest.raises(ReadinessTimeout, match="waiting for container .* to become healthy"):
            dm.wait_until_healthy("dbrescue-mysql-primary", timeout_sec=0.2)


@pytest.mark.errors
def test_row_count_mismatch_detection(tmp_path):
    """Integrity comparison detects row count discrepancy and AlertManager fires RECORD_COUNT_MISMATCH."""
    columns = ["student_id", "name", "email"]
    baseline = [(1, "Alice", "alice@example.com"), (2, "Bob", "bob@example.com")]
    recovered = [(1, "Alice", "alice@example.com")]  # Missing row 2

    res = compare_datasets("students", columns, baseline, recovered)
    assert res.passed is False
    assert res.count_match is False
    assert res.missing_ids == [2]

    # AlertManager integration
    alert_mgr = AlertManager(sqlite_db_path=tmp_path / "integrity.db")
    last_run = {"id": 1, "status": "FAILED", "discrepancy_count": 1}
    evals = alert_mgr.evaluate_all(last_run=last_run)
    count_alert = next(e for e in evals if e.rule_name == "RECORD_COUNT_MISMATCH")
    assert count_alert.state == AlertState.FIRING


@pytest.mark.errors
def test_fingerprint_mismatch_same_count_different_content():
    """Integrity comparison fails when counts match but a single row has modified content."""
    columns = ["student_id", "name", "email"]
    baseline = [(1, "Alice", "alice@example.com"), (2, "Bob", "bob@example.com")]
    recovered = [(1, "Alice", "alice@example.com"), (2, "Bob", "bob_tampered@example.com")]

    res = compare_datasets("students", columns, baseline, recovered)
    assert res.passed is False
    assert res.count_match is True
    assert res.fingerprint_match is False
    assert 2 in res.changed_ids
    assert res.changed_ids[2] == {"email": ("bob@example.com", "bob_tampered@example.com")}


@pytest.mark.errors
def test_backup_process_failure(tmp_path, disposable_ci_config):
    """DumpProcessFailedError is raised when mysqldump exits with error code."""
    mock_docker = MagicMock(spec=DockerManager)
    mock_docker.exec_in_container.return_value = (1, "mysqldump: Got error: 1045 Access denied")

    bm = BackupManager(
        backup_dir=tmp_path / "backups",
        sqlite_db_path=tmp_path / "audit.db",
        docker_manager=mock_docker,
    )

    with pytest.raises(DumpProcessFailedError, match="mysqldump exited with error code 1"):
        bm.create_logical_backup(
            config=disposable_ci_config.primary_mysql,
            database_name="student_db",
        )


@pytest.mark.errors
def test_backup_checksum_mismatch(tmp_path, disposable_ci_config):
    """BackupChecksumMismatchError is raised if backup archive is modified after creation."""
    mock_docker = MagicMock(spec=DockerManager)
    mock_docker.exec_in_container.return_value = (0, "-- MySQL dump\nSELECT 1;\n-- Dump completed\n")

    bm = BackupManager(
        backup_dir=tmp_path / "backups",
        sqlite_db_path=tmp_path / "audit.db",
        docker_manager=mock_docker,
    )

    meta = bm.create_logical_backup(
        config=disposable_ci_config.primary_mysql,
        database_name="student_db",
    )

    # Tamper with the backup file on disk
    with open(meta.file_path, "ab") as f:
        f.write(b"\n-- CORRUPT TAMPERED BYTES --\n")

    with pytest.raises(BackupChecksumMismatchError, match="Checksum Mismatch"):
        bm.verify_backup(meta.backup_id)


@pytest.mark.errors
def test_restore_corrupted_archive_handling(tmp_path, disposable_ci_config):
    """RestoreEngine raises CorruptBackupArchiveError on corrupted gzip stream."""
    sqlite_path = tmp_path / "audit.db"
    audit_logger = AuditLogger(sqlite_path)

    corrupt_file = tmp_path / "corrupt.sql.gz"
    corrupt_file.write_bytes(b"not a valid gzip header content")
    sha = compute_file_sha256(corrupt_file)

    b_id = audit_logger.record_backup(
        filename="corrupt.sql.gz",
        file_path=str(corrupt_file),
        file_size_bytes=len(b"not a valid gzip header content"),
        sha256_checksum=sha,
        compression_type="GZIP",
        database_name="student_db",
        total_tables=1,
        total_rows=10,
        status="VERIFIED_FILE",
    )

    from dbrescue.integrity import IntegrityEngine
    mock_integ = MagicMock(spec=IntegrityEngine)
    engine = RestoreEngine(
        sqlite_db_path=str(sqlite_path),
        integrity_engine=mock_integ,
        audit_logger=audit_logger,
    )

    with pytest.raises(CorruptBackupArchiveError, match="Failed decompressing backup archive"):
        engine.execute_isolated_restore(
            backup_id=b_id,
            target_config=disposable_ci_config.sandbox_mysql,
        )


@pytest.mark.errors
def test_stale_metrics_evaluation(tmp_path):
    """Telemetry older than threshold is marked STALE and triggers METRICS_STALE alert."""
    collector = MonitoringCollector(sqlite_db_path=tmp_path / "audit.db", freshness_threshold_sec=5.0)

    # Fake old snapshot mono timestamp (10 seconds ago)
    old_mono = time.perf_counter() - 10.0
    collector._last_snapshot = TelemetrySnapshot(
        timestamp_utc="2026-10-29T00:00:00Z",
        timestamp_mono=old_mono,
        container=ContainerMetrics(),
        database=DatabaseMetrics(),
        volume=VolumeMetrics(),
        freshness=FreshnessStatus.FRESH,
    )

    telemetry_stale = collector.get_telemetry()
    assert telemetry_stale.freshness == FreshnessStatus.STALE
    assert telemetry_stale.age_seconds >= 10.0

    alert_mgr = AlertManager(sqlite_db_path=tmp_path / "audit.db")
    evals = alert_mgr.evaluate_all(telemetry=telemetry_stale)
    stale_alert = next(e for e in evals if e.rule_name == "METRICS_STALE")
    assert stale_alert.state == AlertState.FIRING
