"""Unit tests for isolated restore engine, parity verification, and defensive cleanup."""

import gzip
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.backup import compute_file_sha256
from dbrescue.config import MySQLConnectionConfig, Status
from dbrescue.integrity import IntegrityEngine
from dbrescue.restore import (
    CorruptBackupArchiveError,
    RestoreEngine,
    UnsafeDatabaseNameError,
)

SAMPLE_SQL = """-- MySQL dump
CREATE TABLE `students` (`id` int, `name` varchar(50));
INSERT INTO `students` VALUES (1, 'Alice'), (2, 'Bob');
-- Dump completed
"""


@pytest.fixture
def restore_env(tmp_path: Path) -> dict:
    sqlite_path = tmp_path / "restore_test.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    audit_logger = AuditLogger(sqlite_path)
    integrity_eng = MagicMock(spec=IntegrityEngine)
    integrity_eng.compute_table_fingerprint.return_value = "table_hash_students_123"

    engine = RestoreEngine(
        sqlite_db_path=str(sqlite_path),
        integrity_engine=integrity_eng,
        audit_logger=audit_logger,
    )

    # Create a real valid .sql.gz archive file on disk
    archive_file = backup_dir / "valid_backup.sql.gz"
    with gzip.open(archive_file, "wb") as gz:
        gz.write(SAMPLE_SQL.encode("utf-8"))

    sha256 = compute_file_sha256(archive_file)
    expected_composite_fp = hashlib.sha256(b"table_hash_students_123").hexdigest()

    backup_id = audit_logger.record_backup(
        filename=archive_file.name,
        file_path=str(archive_file),
        file_size_bytes=archive_file.stat().st_size,
        sha256_checksum=sha256,
        compression_type="GZIP",
        database_name="student_db",
        total_tables=1,
        total_rows=2,
        status="VERIFIED_FILE",
        table_fingerprint=expected_composite_fp,
        metadata_json=json.dumps({"students": 2}),
    )

    db_cfg = MySQLConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="rescue_restore",
        password="RescueRestorePassword2026!",
        database="dbrescue_test",
        root_password="RootPassword2026!",
    )

    return {
        "engine": engine,
        "audit_logger": audit_logger,
        "integrity_engine": integrity_eng,
        "backup_id": backup_id,
        "archive_file": archive_file,
        "db_config": db_cfg,
    }


def test_restore_success_parity_match(restore_env: dict) -> None:
    engine: RestoreEngine = restore_env["engine"]
    audit_logger: AuditLogger = restore_env["audit_logger"]
    backup_id: int = restore_env["backup_id"]
    db_cfg: MySQLConnectionConfig = restore_env["db_config"]

    # Mock MySQL connector
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Multi-statement exec returns list of mock results
    mock_result1 = MagicMock()
    mock_result1.with_rows = False
    mock_cursor.execute.return_value = [mock_result1]

    # Mock information_schema tables query and count query
    mock_cursor.fetchall.side_effect = [
        [("students",)],            # information_schema.TABLES
        [(1, "Alice"), (2, "Bob")], # SELECT * FROM students
    ]
    mock_cursor.fetchone.side_effect = [
        (2,),                       # SELECT COUNT(*) FROM students
    ]

    # Execute isolated restore with mocked connection
    with patch("mysql.connector.connect", return_value=mock_conn):
        result = engine.execute_isolated_restore(
            backup_id=backup_id,
            target_config=db_cfg,
        )

    # Parity match asserts
    assert result.status == Status.SUCCESS
    assert result.verification_status == "VERIFIED"
    assert result.rows_restored == 2
    assert result.error_message is None

    # Backup status in SQLite updated to RESTORE_VERIFIED
    updated_backup = audit_logger.get_backup(backup_id)
    assert updated_backup is not None
    assert updated_backup["status"] == "RESTORE_VERIFIED"

    # Verify DROP DATABASE was called in finally
    calls = [str(call) for call in mock_cursor.execute.call_args_list]
    assert any("DROP DATABASE IF EXISTS" in c for c in calls)


def test_restore_failure_corrupt_archive(restore_env: dict) -> None:
    engine: RestoreEngine = restore_env["engine"]
    backup_id: int = restore_env["backup_id"]
    archive_file: Path = restore_env["archive_file"]
    db_cfg: MySQLConnectionConfig = restore_env["db_config"]

    # Tamper with archive file on disk
    archive_file.write_bytes(b"corrupted_content")

    with pytest.raises(CorruptBackupArchiveError):
        engine.execute_isolated_restore(
            backup_id=backup_id,
            target_config=db_cfg,
        )


def test_restore_failure_checksum_match_but_content_mismatch(restore_env: dict) -> None:
    """Checksum matches, but restored row counts differ from backup baseline."""
    engine: RestoreEngine = restore_env["engine"]
    audit_logger: AuditLogger = restore_env["audit_logger"]
    backup_id: int = restore_env["backup_id"]
    db_cfg: MySQLConnectionConfig = restore_env["db_config"]

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    mock_result1 = MagicMock()
    mock_result1.with_rows = False
    mock_cursor.execute.return_value = [mock_result1]

    # Simulate restoring only 1 row instead of expected 2
    mock_cursor.fetchall.side_effect = [
        [("students",)],    # information_schema.TABLES
        [(1, "Alice")],     # SELECT * FROM students
    ]
    mock_cursor.fetchone.side_effect = [
        (1,),               # SELECT COUNT(*) returns 1 instead of 2!
    ]

    with patch("mysql.connector.connect", return_value=mock_conn):
        result = engine.execute_isolated_restore(
            backup_id=backup_id,
            target_config=db_cfg,
        )

    assert result.status == Status.FAILED
    assert result.verification_status == "MISMATCH"
    assert "count_match=False" in (result.error_message or "")

    # Backup status must NOT become RESTORE_VERIFIED
    backup_rec = audit_logger.get_backup(backup_id)
    assert backup_rec is not None
    assert backup_rec["status"] != "RESTORE_VERIFIED"


def test_restore_failure_sql_execution_error(restore_env: dict) -> None:
    """When SQL execution fails, isolated DB must still be dropped and status is FAILED."""
    engine: RestoreEngine = restore_env["engine"]
    backup_id: int = restore_env["backup_id"]
    db_cfg: MySQLConnectionConfig = restore_env["db_config"]

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Simulate SQL execution crash
    mock_cursor.execute.side_effect = Exception("Table 'broken' doesn't exist")

    with patch("mysql.connector.connect", return_value=mock_conn):
        result = engine.execute_isolated_restore(
            backup_id=backup_id,
            target_config=db_cfg,
        )

    assert result.status == Status.FAILED
    assert result.verification_status == "FAILED"
    assert "Table 'broken' doesn't exist" in (result.error_message or "")


def test_restore_rejects_unsafe_database_name(restore_env: dict) -> None:
    engine: RestoreEngine = restore_env["engine"]

    # Reject non-isolated prefixes
    with pytest.raises(UnsafeDatabaseNameError):
        engine._validate_safe_restore_db("student_db")

    with pytest.raises(UnsafeDatabaseNameError):
        engine._validate_safe_restore_db("mysql")

    # Accept isolated prefix
    assert engine._validate_safe_restore_db("dbrescue_restore_test123") == "dbrescue_restore_test123"
