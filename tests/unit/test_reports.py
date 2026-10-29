"""Unit tests for ReportLab PDF report generator and JSON metadata emitter."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.reports import (
    RecoveryReportGenerator,
    ReportDataIncompleteError,
)


@pytest.fixture
def sample_run_environment(tmp_path: Path):
    """Sets up an audit store with a real disaster recovery run."""
    sqlite_path = tmp_path / "reports_test.sqlite3"
    reports_dir = tmp_path / "reports_output"
    reports_dir.mkdir(parents=True, exist_ok=True)

    audit_logger = AuditLogger(sqlite_path)
    run_uuid = str(uuid.uuid4())

    # 1. Create run
    run_id = audit_logger.create_run(
        run_uuid=run_uuid,
        target_volume="dbrescue_mysql_data",
        primary_container="dbrescue-mysql-primary",
        trigger_type="MANUAL_UI",
        initial_stage="INIT_RUN",
    )
    audit_logger.complete_run(
        run_id=run_id,
        status="SUCCESS",
        final_stage="FINALIZE_RUN",
    )

    # 2. Add snapshots
    audit_logger.record_snapshot(
        run_id=run_id,
        database_name="student_db",
        total_tables=1,
        total_rows=100,
        total_data_bytes=20480,
        fingerprint_hash="baseline_sha256_hash_1234567890abcdef",
        is_baseline=True,
    )
    audit_logger.record_snapshot(
        run_id=run_id,
        database_name="student_db",
        total_tables=1,
        total_rows=100,
        total_data_bytes=20480,
        fingerprint_hash="baseline_sha256_hash_1234567890abcdef",
        is_baseline=False,
    )

    # 3. Add audit events
    audit_logger.log_event(
        run_id=run_id,
        stage="INIT_RUN",
        event_type="STAGE_TRANSITION",
        severity="INFO",
        message="Initialized DR run",
        duration_ms=12.5,
    )
    audit_logger.log_event(
        run_id=run_id,
        stage="AUDIT_INTEGRITY",
        event_type="INTEGRITY_VERIFIED",
        severity="INFO",
        message="100% parity verified",
        duration_ms=45.2,
    )

    # 4. Add backup record
    audit_logger.record_backup(
        filename="backup_student_db.sql.gz",
        file_path="/var/backups/backup_student_db.sql.gz",
        file_size_bytes=10240,
        sha256_checksum="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        compression_type="GZIP",
        database_name="student_db",
        total_tables=1,
        total_rows=100,
        status="VERIFIED_FILE",
        run_id=run_id,
    )

    return {
        "sqlite_path": sqlite_path,
        "reports_dir": reports_dir,
        "run_uuid": run_uuid,
        "run_id": run_id,
    }


@pytest.mark.unit
def test_compile_pdf_report_success(sample_run_environment):
    """Verifies that PDF report is compiled on disk and companion JSON is emitted."""
    generator = RecoveryReportGenerator(
        sqlite_db_path=sample_run_environment["sqlite_path"],
        reports_dir=sample_run_environment["reports_dir"],
    )

    pdf_path = generator.compile_pdf_report(sample_run_environment["run_uuid"])

    assert pdf_path.exists()
    assert pdf_path.is_file()
    assert pdf_path.stat().st_size > 1000  # Multi-page or detailed PDF

    # Accompanying JSON metadata
    json_path = pdf_path.with_suffix(".json")
    assert json_path.exists()

    with open(json_path, encoding="utf-8") as jf:
        metadata = json.load(jf)
        assert metadata["run_uuid"] == sample_run_environment["run_uuid"]
        assert metadata["passed"] is True
        assert metadata["status"] == "SUCCESS"
        assert metadata["total_events"] == 2
        assert metadata["backups_count"] == 1


@pytest.mark.unit
def test_report_generation_refuses_nonexistent_run(sample_run_environment):
    """Attempting to compile a report for a nonexistent run UUID raises ReportDataIncompleteError."""
    generator = RecoveryReportGenerator(
        sqlite_db_path=sample_run_environment["sqlite_path"],
        reports_dir=sample_run_environment["reports_dir"],
    )

    nonexistent_uuid = str(uuid.uuid4())
    with pytest.raises(ReportDataIncompleteError, match="No disaster recovery run found"):
        generator.compile_pdf_report(nonexistent_uuid)
