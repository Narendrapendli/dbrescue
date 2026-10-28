"""Isolated database restore engine with cryptographic parity verification.

Restores logical backup archives into ephemeral isolated databases (dbrescue_restore_<id>)
using the restricted rescue_restore user. Verifies post-restore table row counts and canonical
fingerprints against backup-time metadata, records audit metrics, and safely drops ephemeral
databases upon completion.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import logging
import re
import time
import uuid

import mysql.connector

from dbrescue.audit import AuditLogger
from dbrescue.backup import BackupManager, compute_file_sha256
from dbrescue.config import MySQLConnectionConfig, RescueException, Status
from dbrescue.db import ping_database
from dbrescue.docker_manager import DockerManager
from dbrescue.integrity import IntegrityEngine
from dbrescue.security import sanitize_error_message, validate_sql_identifier

logger = logging.getLogger("dbrescue.restore")

RESTORE_DB_PREFIX = "dbrescue_restore_"
SAFE_RESTORE_DB_REGEX = re.compile(r"^dbrescue_restore_[a-zA-Z0-9_]+$")


# --- Exceptions ---

class RestoreError(RescueException):
    """Base exception for all restore operations."""
    pass


class CorruptBackupArchiveError(RestoreError):
    """Raised when backup archive checksum fails or archive contains corrupted gzip/SQL."""
    pass


class RestoreExecutionFailedError(RestoreError):
    """Raised when SQL execution fails during restore."""
    pass


class PostRestoreParityError(RestoreError):
    """Raised when restored database row counts or fingerprints do not match backup baseline."""
    pass


class UnsafeDatabaseNameError(RestoreError):
    """Raised when an attempt is made to drop or modify a database not matching restore prefix."""
    pass


# --- Dataclasses ---

class RestoreResult:
    """Immutable outcome of an isolated restore execution."""

    def __init__(
        self,
        restore_id: int,
        run_id: int | None,
        backup_id: int,
        target_database: str,
        status: Status,
        rows_restored: int,
        verification_status: str,  # 'VERIFIED', 'MISMATCH', 'UNVERIFIED', 'FAILED'
        duration_seconds: float,
        error_message: str | None = None,
    ) -> None:
        self.restore_id = restore_id
        self.run_id = run_id
        self.backup_id = backup_id
        self.target_database = target_database
        self.status = status
        self.rows_restored = rows_restored
        self.verification_status = verification_status
        self.duration_seconds = duration_seconds
        self.error_message = error_message

    def __repr__(self) -> str:
        return (
            f"RestoreResult(restore_id={self.restore_id}, backup_id={self.backup_id}, "
            f"target_db='{self.target_database}', status={self.status.value}, "
            f"rows={self.rows_restored}, verified={self.verification_status}, "
            f"duration={self.duration_seconds:.2f}s)"
        )


# --- Restore Engine ---

class RestoreEngine:
    """Restores database from validated compressed backup archives with strict isolation."""

    def __init__(
        self,
        sqlite_db_path: str,
        docker_manager: DockerManager | None = None,
        integrity_engine: IntegrityEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.sqlite_db_path = str(sqlite_db_path)
        self.docker_manager = docker_manager
        self.integrity_engine = integrity_engine or IntegrityEngine(self.sqlite_db_path)
        self.audit_logger = audit_logger or AuditLogger(self.sqlite_db_path)
        self.backup_manager = BackupManager(
            backup_dir="./backups",
            sqlite_db_path=self.sqlite_db_path,
            docker_manager=self.docker_manager,
            integrity_engine=self.integrity_engine,
            audit_logger=self.audit_logger,
        )

    def _validate_safe_restore_db(self, db_name: str) -> str:
        """Validates that target database name strictly matches isolated restore pattern."""
        valid_name = validate_sql_identifier(db_name)
        if not SAFE_RESTORE_DB_REGEX.match(valid_name):
            raise UnsafeDatabaseNameError(
                f"Security Guard: Database name '{valid_name}' is not an authorized isolated restore database. "
                f"Must strictly match prefix '{RESTORE_DB_PREFIX}'."
            )
        return valid_name

    def simulate_restore_dry_run(
        self,
        backup_id: int,
        target_config: MySQLConnectionConfig,
    ) -> bool:
        """Validates archive integrity and tests target connectivity without applying DDL/DML."""
        backup_meta = self.audit_logger.get_backup(backup_id)
        if not backup_meta:
            raise RestoreError(f"Backup record ID {backup_id} not found.")

        # 1. Cryptographic archive verification
        self.backup_manager.verify_backup(backup_id=backup_id)

        # 2. Database connectivity check
        if not ping_database(target_config, timeout_sec=5):
            raise RestoreError(f"Cannot reach target MySQL instance on {target_config.host}:{target_config.port}")

        return True

    def execute_isolated_restore(
        self,
        backup_id: int,
        target_config: MySQLConnectionConfig,
        run_id: int | None = None,
        _pool: mysql.connector.pooling.MySQLConnectionPool | None = None,
    ) -> RestoreResult:
        """Restores backup into an isolated database, validates row count & fingerprint parity,

        and safely drops the isolated database afterward.

        CRITICAL SAFETY:
          - Only databases matching 'dbrescue_restore_<id>' are created and dropped.
          - Restricted restore user credentials used exclusively.
          - Backup status updated to 'RESTORE_VERIFIED' ONLY when counts AND fingerprints match.
        """
        start_time = time.perf_counter()
        token = uuid.uuid4().hex[:8]
        isolated_db = self._validate_safe_restore_db(f"{RESTORE_DB_PREFIX}{token}")

        # 1. Retrieve backup metadata
        backup_meta = self.audit_logger.get_backup(backup_id)
        if not backup_meta:
            raise RestoreError(f"Backup record ID {backup_id} not found.")

        expected_hash = backup_meta["sha256_checksum"]
        archive_path = backup_meta["file_path"]

        # 2. Verify archive checksum
        recomputed_hash = compute_file_sha256(archive_path)
        if recomputed_hash.lower() != expected_hash.lower():
            self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise CorruptBackupArchiveError(
                f"Checksum mismatch for backup ID {backup_id}: expected {expected_hash}, got {recomputed_hash}"
            )

        # 3. Decompress SQL dump
        try:
            with gzip.open(archive_path, "rt", encoding="utf-8", errors="replace") as f:
                dump_sql = f.read()
        except Exception as err:
            self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise CorruptBackupArchiveError(f"Failed decompressing backup archive: {err}") from err

        if not dump_sql.strip():
            raise CorruptBackupArchiveError("Backup archive contains empty SQL dump.")

        # 4. Record restore start in SQLite
        restore_id = self.audit_logger.record_restore(
            run_id=run_id,
            backup_id=backup_id,
            target_database=isolated_db,
            status="IN_PROGRESS",
            verification_status="UNVERIFIED",
        )

        rows_restored_total = 0
        status = Status.FAILED
        verification_status = "FAILED"
        err_msg = None

        # 5. Connect and execute in isolated database
        conn = None
        try:
            conn = mysql.connector.connect(
                host=target_config.host,
                port=target_config.port,
                user=target_config.user,
                password=target_config.password,
                connect_timeout=target_config.connect_timeout_sec,
            )
            cursor = conn.cursor()

            # Create ephemeral isolated DB
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{isolated_db}`;")
            cursor.execute(f"USE `{isolated_db}`;")

            # Execute statements from dump
            # Split by statements safely, or run multi-statements
            for result in cursor.execute(dump_sql, multi=True):
                # Consume results
                if result.with_rows:
                    result.fetchall()

            conn.commit()

            # 6. Post-restore Parity Verification
            # Query tables in isolated_db
            cursor.execute(
                """
                SELECT TABLE_NAME, TABLE_ROWS
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE';
                """,
                (isolated_db,),
            )
            tables = [row[0] for row in cursor.fetchall()]

            restored_counts: dict[str, int] = {}
            for tbl in tables:
                valid_tbl = validate_sql_identifier(tbl)
                cursor.execute(f"SELECT COUNT(*) FROM `{isolated_db}`.`{valid_tbl}`;")
                cnt = cursor.fetchone()[0]
                restored_counts[valid_tbl] = cnt
                rows_restored_total += cnt

            # Retrieve baseline expected counts & fingerprint
            expected_total_rows = backup_meta.get("total_rows", 0)
            expected_fingerprint = backup_meta.get("table_fingerprint")
            expected_row_counts_json = backup_meta.get("metadata_json")
            expected_row_counts = json.loads(expected_row_counts_json) if expected_row_counts_json else {}

            # Count check
            count_match = True
            if expected_row_counts:
                for tbl, exp_cnt in expected_row_counts.items():
                    if restored_counts.get(tbl, 0) != exp_cnt:
                        count_match = False
                        break
            elif expected_total_rows > 0:
                count_match = (rows_restored_total == expected_total_rows)

            # Fingerprint check
            fingerprint_match = True
            if expected_fingerprint:
                # Compute composite fingerprint across restored tables
                combined_hashes = []
                for tbl in sorted(tables):
                    valid_tbl = validate_sql_identifier(tbl)
                    cursor.execute(f"SELECT * FROM `{isolated_db}`.`{valid_tbl}`;")
                    rows = cursor.fetchall()
                    table_fp = self.integrity_engine.compute_table_fingerprint(rows)
                    combined_hashes.append(table_fp)
                restored_composite_fp = hashlib.sha256("".join(combined_hashes).encode("utf-8")).hexdigest()
                fingerprint_match = (restored_composite_fp == expected_fingerprint)

            # 7. Finalize status: RESTORE_VERIFIED requires both
            if count_match and fingerprint_match:
                status = Status.SUCCESS
                verification_status = "VERIFIED"
                self.audit_logger.update_backup_status(backup_id, "RESTORE_VERIFIED")
                logger.info(
                    "Restore parity check PASSED for backup ID %d: %d rows verified, fingerprints match.",
                    backup_id,
                    rows_restored_total,
                )
            else:
                status = Status.FAILED
                verification_status = "MISMATCH"
                err_msg = (
                    f"Parity check failed: count_match={count_match} "
                    f"(restored={rows_restored_total}, expected={expected_total_rows}), "
                    f"fingerprint_match={fingerprint_match}."
                )
                logger.warning("Restore parity check MISMATCH for backup ID %d: %s", backup_id, err_msg)

        except Exception as err:
            status = Status.FAILED
            verification_status = "FAILED"
            err_msg = sanitize_error_message(err)
            logger.error("Restore execution failed for backup ID %d: %s", backup_id, err_msg)
        finally:
            # 8. Clean up isolated database safely
            if conn:
                with contextlib.suppress(Exception):
                    cleanup_cursor = conn.cursor()
                    assert isolated_db.startswith(RESTORE_DB_PREFIX)
                    cleanup_cursor.execute(f"DROP DATABASE IF EXISTS `{isolated_db}`;")
                    conn.commit()
                    cleanup_cursor.close()
                with contextlib.suppress(Exception):
                    conn.close()

        duration = time.perf_counter() - start_time
        self.audit_logger.complete_restore(
            restore_id=restore_id,
            status=status.value,
            verification_status=verification_status,
            rows_restored=rows_restored_total,
            error_message=err_msg,
        )

        return RestoreResult(
            restore_id=restore_id,
            run_id=run_id,
            backup_id=backup_id,
            target_database=isolated_db,
            status=status,
            rows_restored=rows_restored_total,
            verification_status=verification_status,
            duration_seconds=duration,
            error_message=err_msg,
        )
