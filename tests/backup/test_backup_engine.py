"""Unit tests for logical backup creation, checksumming, GZIP streaming, and verification."""

import gzip
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.backup import (
    BackupChecksumMismatchError,
    BackupCorruptError,
    BackupManager,
    BackupTruncatedError,
    DumpProcessFailedError,
    compute_file_sha256,
)
from dbrescue.config import MySQLConnectionConfig
from dbrescue.docker_manager import DockerManager
from dbrescue.integrity import IntegrityEngine

VALID_DUMP_SQL = """-- MySQL dump 10.13  Distrib 8.4.3, for Linux (x86_64)
-- Host: 127.0.0.1    Database: student_db
-- ------------------------------------------------------
-- Server version	8.4.3

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8mb4 */;

DROP TABLE IF EXISTS `students`;
CREATE TABLE `students` (
  `id` int NOT NULL AUTO_INCREMENT,
  `student_id` varchar(20) NOT NULL,
  `first_name` varchar(50) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

LOCK TABLES `students` WRITE;
INSERT INTO `students` VALUES (1,'STU-001','Alice'),(2,'STU-002','Bob');
UNLOCK TABLES;

-- Dump completed on 2026-10-29 00:00:00
"""


@pytest.fixture
def backup_env(tmp_path: Path) -> dict:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = tmp_path / "test_backup_audit.sqlite3"

    audit_logger = AuditLogger(sqlite_path)
    integrity_eng = MagicMock(spec=IntegrityEngine)
    docker_mgr = MagicMock(spec=DockerManager)

    # Mock successful mysqldump
    docker_mgr.exec_in_container.return_value = (0, VALID_DUMP_SQL)

    manager = BackupManager(
        backup_dir=backup_dir,
        sqlite_db_path=sqlite_path,
        docker_manager=docker_mgr,
        integrity_engine=integrity_eng,
        audit_logger=audit_logger,
    )

    db_cfg = MySQLConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="rescue_backup",
        password="RescueBackupPassword2026!",
        database="student_db",
        root_password="RootPassword2026!",
    )

    return {
        "manager": manager,
        "docker_manager": docker_mgr,
        "audit_logger": audit_logger,
        "backup_dir": backup_dir,
        "db_config": db_cfg,
    }


def test_create_logical_backup_success(backup_env: dict) -> None:
    manager: BackupManager = backup_env["manager"]
    docker_mgr: MagicMock = backup_env["docker_manager"]
    db_cfg: MySQLConnectionConfig = backup_env["db_config"]

    metadata = manager.create_logical_backup(
        config=db_cfg,
        database_name="student_db",
        total_tables=1,
        total_rows=2,
        table_fingerprint="abc123hash",
        table_row_counts={"students": 2},
    )

    assert metadata.backup_id > 0
    assert metadata.status == "CREATED"
    assert metadata.file_path.exists()
    assert metadata.file_size_bytes > 0
    assert len(metadata.sha256_checksum) == 64
    assert metadata.table_fingerprint == "abc123hash"
    assert metadata.table_row_counts == {"students": 2}

    # Verify docker exec call did NOT expose password on command line
    docker_mgr.exec_in_container.assert_called_once()
    args, kwargs = docker_mgr.exec_in_container.call_args
    cmd = kwargs.get("command") or args[1]
    env = kwargs.get("environment") or args[2]

    # Command line must not contain password
    assert db_cfg.password not in cmd
    assert any("--single-transaction" in c for c in cmd)
    assert any("--routines" in c for c in cmd)

    # Password must be in isolated environment dict
    assert env.get("MYSQL_PWD") == db_cfg.password


def test_verify_backup_success(backup_env: dict) -> None:
    manager: BackupManager = backup_env["manager"]
    audit_logger: AuditLogger = backup_env["audit_logger"]
    db_cfg: MySQLConnectionConfig = backup_env["db_config"]

    metadata = manager.create_logical_backup(
        config=db_cfg,
        database_name="student_db",
    )

    # Initial status is CREATED
    assert metadata.status == "CREATED"

    # Verify backup
    is_valid = manager.verify_backup(backup_id=metadata.backup_id)
    assert is_valid is True

    # Status must now be strictly VERIFIED_FILE (never restorable yet)
    updated = audit_logger.get_backup(metadata.backup_id)
    assert updated is not None
    assert updated["status"] == "VERIFIED_FILE"


def test_verify_backup_checksum_mismatch(backup_env: dict) -> None:
    manager: BackupManager = backup_env["manager"]
    audit_logger: AuditLogger = backup_env["audit_logger"]
    db_cfg: MySQLConnectionConfig = backup_env["db_config"]

    metadata = manager.create_logical_backup(
        config=db_cfg,
        database_name="student_db",
    )

    # Tamper with archive file on disk
    with open(metadata.file_path, "ab") as f:
        f.write(b"\x00\xffCORRUPT_BYTES")

    # Verification must raise BackupChecksumMismatchError
    with pytest.raises(BackupChecksumMismatchError) as exc_info:
        manager.verify_backup(backup_id=metadata.backup_id)
    assert "Checksum Mismatch" in str(exc_info.value)

    # Status in audit store must be marked CORRUPT
    updated = audit_logger.get_backup(metadata.backup_id)
    assert updated is not None
    assert updated["status"] == "CORRUPT"


def test_verify_backup_corrupted_gzip(backup_env: dict) -> None:
    manager: BackupManager = backup_env["manager"]
    audit_logger: AuditLogger = backup_env["audit_logger"]
    db_cfg: MySQLConnectionConfig = backup_env["db_config"]

    metadata = manager.create_logical_backup(
        config=db_cfg,
        database_name="student_db",
    )

    # Overwrite file with invalid gzip data but update recorded checksum to match corrupt data
    corrupted_data = b"NOT_A_GZIP_FILE_AT_ALL_JUST_PLAIN_GARBAGE"
    metadata.file_path.write_bytes(corrupted_data)
    new_sha256 = compute_file_sha256(metadata.file_path)

    # Manually update recorded sha256 to simulate checksum match on corrupt archive
    conn = audit_logger._get_connection()
    conn.execute("UPDATE backups SET sha256_checksum = ? WHERE id = ?;", (new_sha256, metadata.backup_id))
    conn.commit()
    conn.close()

    # Verification must catch gzip corruption
    with pytest.raises(BackupCorruptError) as exc_info:
        manager.verify_backup(backup_id=metadata.backup_id)
    assert "failed GZIP decompression" in str(exc_info.value)

    updated = audit_logger.get_backup(metadata.backup_id)
    assert updated is not None
    assert updated["status"] == "CORRUPT"


def test_verify_backup_truncated_dump(backup_env: dict) -> None:
    manager: BackupManager = backup_env["manager"]
    audit_logger: AuditLogger = backup_env["audit_logger"]
    db_cfg: MySQLConnectionConfig = backup_env["db_config"]

    metadata = manager.create_logical_backup(
        config=db_cfg,
        database_name="student_db",
    )

    # Create a truncated dump (has header, but truncated mid-statement, no completion marker)
    truncated_sql = "-- MySQL dump 10.13\nCREATE TABLE broken (id INT"
    with gzip.open(metadata.file_path, "wb") as gz:
        gz.write(truncated_sql.encode("utf-8"))

    new_sha = compute_file_sha256(metadata.file_path)
    conn = audit_logger._get_connection()
    conn.execute("UPDATE backups SET sha256_checksum = ? WHERE id = ?;", (new_sha, metadata.backup_id))
    conn.commit()
    conn.close()

    # Must raise BackupTruncatedError
    with pytest.raises(BackupTruncatedError) as exc_info:
        manager.verify_backup(backup_id=metadata.backup_id)
    assert "truncated" in str(exc_info.value)

    updated = audit_logger.get_backup(metadata.backup_id)
    assert updated is not None
    assert updated["status"] == "CORRUPT"


def test_backup_failure_exec_error(backup_env: dict) -> None:
    manager: BackupManager = backup_env["manager"]
    docker_mgr: MagicMock = backup_env["docker_manager"]
    db_cfg: MySQLConnectionConfig = backup_env["db_config"]

    # Simulate mysqldump process crash
    docker_mgr.exec_in_container.return_value = (2, "mysqldump: Got error: 1045: Access denied for user")

    with pytest.raises(DumpProcessFailedError) as exc_info:
        manager.create_logical_backup(
            config=db_cfg,
            database_name="student_db",
        )
    assert "mysqldump exited with error code 2" in str(exc_info.value)
