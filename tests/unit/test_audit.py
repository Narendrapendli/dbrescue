"""Unit tests for SQLite audit store, WAL mode, foreign keys, and export helpers."""

import csv
import json
import uuid
from pathlib import Path

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.security import SecurityValidationError


@pytest.fixture
def temp_audit_logger(tmp_path: Path) -> AuditLogger:
    db_path = tmp_path / "test_audit.sqlite3"
    return AuditLogger(db_path)


def test_audit_db_init_and_tables(temp_audit_logger: AuditLogger) -> None:
    conn = temp_audit_logger._get_connection()
    try:
        # Check WAL mode
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        # Check foreign keys enabled
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1

        # Check all 9 tables exist
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
            ).fetchall()
        }
        expected_tables = {
            "runs",
            "snapshots",
            "snapshot_rows",
            "backups",
            "restores",
            "audit_events",
            "alerts",
            "settings",
            "metrics_samples",
        }
        assert expected_tables.issubset(tables)
    finally:
        conn.close()


def test_audit_create_run_and_update_stage(temp_audit_logger: AuditLogger) -> None:
    run_uuid = str(uuid.uuid4())
    run_id = temp_audit_logger.create_run(
        run_uuid=run_uuid,
        target_volume="mysql_data",
        primary_container="mysql_prod",
        trigger_type="MANUAL_CLI",
        initial_stage="INIT_RUN",
    )
    assert isinstance(run_id, int)
    assert run_id > 0

    temp_audit_logger.update_run_stage(run_id, "CHECK_DOCKER")

    conn = temp_audit_logger._get_connection()
    try:
        row = conn.execute(
            "SELECT current_stage, status FROM runs WHERE id = ?;", (run_id,)
        ).fetchone()
        assert row["current_stage"] == "CHECK_DOCKER"
        assert row["status"] == "RUNNING"
    finally:
        conn.close()


def test_audit_create_run_invalid_uuid(temp_audit_logger: AuditLogger) -> None:
    with pytest.raises(SecurityValidationError):
        temp_audit_logger.create_run(
            run_uuid="invalid-uuid-format-1234",
            target_volume="mysql_data",
            primary_container="mysql_prod",
        )


def test_audit_complete_run(temp_audit_logger: AuditLogger) -> None:
    run_uuid = str(uuid.uuid4())
    run_id = temp_audit_logger.create_run(
        run_uuid=run_uuid,
        target_volume="mysql_data",
        primary_container="mysql_prod",
    )

    temp_audit_logger.complete_run(
        run_id=run_id,
        status="SUCCESS",
        final_stage="FINALIZE_RUN",
        error_message=None,
    )

    conn = temp_audit_logger._get_connection()
    try:
        row = conn.execute(
            "SELECT status, final_stage, current_stage, completed_at, error_message FROM runs WHERE id = ?;",
            (run_id,),
        ).fetchone()
        assert row["status"] == "SUCCESS"
        assert row["final_stage"] == "FINALIZE_RUN"
        assert row["current_stage"] == "FINALIZE_RUN"
        assert row["completed_at"] is not None
        assert row["error_message"] is None
    finally:
        conn.close()


def test_audit_log_event_and_trail(temp_audit_logger: AuditLogger) -> None:
    run_uuid = str(uuid.uuid4())
    run_id = temp_audit_logger.create_run(
        run_uuid=run_uuid,
        target_volume="mysql_data",
        primary_container="mysql_prod",
    )

    event1 = temp_audit_logger.log_event(
        run_id=run_id,
        stage="INIT_RUN",
        event_type="DISCOVERY",
        severity="INFO",
        message="Starting pre-flight checks",
        duration_ms=12.5,
        details={"engine": "InnoDB"},
    )
    assert event1.event_id > 0
    assert event1.stage == "INIT_RUN"
    assert event1.details["engine"] == "InnoDB"

    event2 = temp_audit_logger.log_event(
        run_id=run_id,
        stage="CHECK_DOCKER",
        event_type="VALIDATION",
        severity="WARNING",
        message="Docker ping high latency",
        duration_ms=45.2,
    )
    assert event2.event_id > event1.event_id

    trail = temp_audit_logger.get_run_audit_trail(run_id)
    assert len(trail) == 2
    assert trail[0].message == "Starting pre-flight checks"
    assert trail[1].message == "Docker ping high latency"
    assert trail[0].duration_ms == 12.5


def test_audit_export_json_and_csv(temp_audit_logger: AuditLogger, tmp_path: Path) -> None:
    run_uuid = str(uuid.uuid4())
    run_id = temp_audit_logger.create_run(
        run_uuid=run_uuid,
        target_volume="mysql_data",
        primary_container="mysql_prod",
    )

    temp_audit_logger.log_event(
        run_id=run_id,
        stage="INIT_RUN",
        event_type="BOOT",
        severity="INFO",
        message="Boot message",
        duration_ms=10.0,
        details={"k": "v"},
    )

    # Export JSON
    json_path = tmp_path / "audit_export.json"
    exported_json = temp_audit_logger.export_audit_log_json(run_id, json_path)
    assert exported_json.exists()

    with open(exported_json, encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) == 1
        assert data[0]["message"] == "Boot message"
        assert data[0]["details"] == {"k": "v"}

    # Export CSV
    csv_path = tmp_path / "audit_export.csv"
    exported_csv = temp_audit_logger.export_run_csv(run_id, csv_path)
    assert exported_csv.exists()

    with open(exported_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["stage"] == "INIT_RUN"
        assert rows[0]["message"] == "Boot message"


def test_audit_snapshot_and_rows(temp_audit_logger: AuditLogger) -> None:
    run_uuid = str(uuid.uuid4())
    run_id = temp_audit_logger.create_run(
        run_uuid=run_uuid,
        target_volume="mysql_data",
        primary_container="mysql_prod",
    )

    snap_id = temp_audit_logger.record_snapshot(
        run_id=run_id,
        database_name="student_db",
        total_tables=3,
        total_rows=160,
        total_data_bytes=40960,
        fingerprint_hash="a" * 64,
        is_baseline=True,
    )
    assert snap_id > 0

    row_id = temp_audit_logger.record_snapshot_table(
        snapshot_id=snap_id,
        table_name="students",
        row_count=100,
        data_bytes=20480,
        table_checksum="b" * 64,
        check_table_status="OK",
    )
    assert row_id > 0


def test_audit_alert_and_settings(temp_audit_logger: AuditLogger) -> None:
    # Alerts
    alert_id = temp_audit_logger.record_alert(
        alert_name="CORRUPT_PAGE",
        severity="CRITICAL",
        message="Corrupt InnoDB page detected",
    )
    assert alert_id > 0

    resolved = temp_audit_logger.resolve_alert(alert_id)
    assert resolved is True

    # Settings
    temp_audit_logger.set_setting("retention_days", "30", "Audit trail retention limit")
    val = temp_audit_logger.get_setting("retention_days")
    assert val == "30"

    # Fallback default
    missing = temp_audit_logger.get_setting("non_existent_key", default="default_val")
    assert missing == "default_val"


def test_audit_parameterization_safety(temp_audit_logger: AuditLogger) -> None:
    injection_uuid = str(uuid.uuid4())
    run_id = temp_audit_logger.create_run(
        run_uuid=injection_uuid,
        target_volume="mysql_data",
        primary_container="mysql_prod",
    )

    injection_str = "'; DROP TABLE runs; --"
    event = temp_audit_logger.log_event(
        run_id=run_id,
        stage="AUDIT_INTEGRITY",
        event_type="SQL_TEST",
        severity="INFO",
        message=injection_str,
    )
    assert event.message == injection_str

    # Verify table 'runs' still exists and was not dropped
    conn = temp_audit_logger._get_connection()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM runs;")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()
