"""Logical backup generation, streaming compression, and cryptographic verification engine.

Executes mysqldump inside the MySQL container via DockerManager allowlisted exec
with zero credentials exposed on the command line (MYSQL_PWD passed safely via environment).
Streams archives through gzip compression, calculates SHA-256 checksums, enforces
restricted file permissions, and persists metadata in SQLite.
"""

from __future__ import annotations

import contextlib
import datetime
import gzip
import hashlib
import json
import logging
import os
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dbrescue.audit import AuditLogger
from dbrescue.config import MySQLConnectionConfig, RescueException
from dbrescue.docker_manager import DockerManager
from dbrescue.integrity import IntegrityEngine
from dbrescue.security import sanitize_error_message, validate_sql_identifier

logger = logging.getLogger("dbrescue.backup")


# --- Exceptions ---


class BackupError(RescueException):
    """Base exception for all backup subsystem failures."""

    pass


class DumpProcessFailedError(BackupError):
    """Raised when mysqldump exits with a non-zero exit code or fails execution."""

    pass


class BackupChecksumMismatchError(BackupError):
    """Raised when recomputed archive SHA-256 differs from recorded metadata."""

    pass


class BackupStorageFullError(BackupError):
    """Raised when host disk runs out of space while writing backup archive."""

    pass


class BackupCorruptError(BackupError):
    """Raised when backup archive contains corrupted gzip data or invalid syntax."""

    pass


class BackupTruncatedError(BackupError):
    """Raised when dump is incomplete or missing terminating SQL markers."""

    pass


# --- Dataclasses ---


@dataclass(frozen=True)
class BackupMetadata:
    """Standardized metadata record for a completed backup artifact."""

    backup_id: int
    run_id: int | None
    filename: str
    file_path: Path
    file_size_bytes: int
    sha256_checksum: str
    compression_type: str
    database_name: str
    total_tables: int
    total_rows: int
    status: str  # 'CREATED', 'VERIFIED_FILE', 'RESTORE_VERIFIED', 'CORRUPT', 'EXPIRED'
    created_at: str
    table_fingerprint: str | None = None
    table_row_counts: dict[str, int] = field(default_factory=dict)


# --- Helper Functions ---


def compute_file_sha256(file_path: Path | str, chunk_size: int = 65536) -> str:
    """Streams file contents and computes SHA-256 hex digest."""
    path = Path(file_path).resolve()
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def restrict_file_permissions(file_path: Path) -> None:
    """Sets file permissions to owner read/write only (0600 on POSIX)."""
    with contextlib.suppress(Exception):
        if os.name == "posix":
            os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
        elif os.name == "nt":
            # On Windows, best-effort readonly removal or simple user rw
            os.chmod(file_path, stat.S_IREAD | stat.S_IWRITE)


# --- Backup Manager ---


class BackupManager:
    """Manages creation, compression, hashing, and verification of MySQL logical dumps."""

    def __init__(
        self,
        backup_dir: Path | str,
        sqlite_db_path: Path | str,
        docker_manager: DockerManager | None = None,
        integrity_engine: IntegrityEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.backup_dir = Path(backup_dir).resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_db_path = Path(sqlite_db_path).resolve()
        self.docker_manager = docker_manager
        self.integrity_engine = integrity_engine or IntegrityEngine(self.sqlite_db_path)
        self.audit_logger = audit_logger or AuditLogger(self.sqlite_db_path)

    def create_logical_backup(
        self,
        config: MySQLConnectionConfig,
        database_name: str,
        container_name: str = "mysql_prod",
        run_id: int | None = None,
        total_tables: int = 0,
        total_rows: int = 0,
        table_fingerprint: str | None = None,
        table_row_counts: dict[str, int] | None = None,
    ) -> BackupMetadata:
        """Executes mysqldump within container, compresses to gzip, computes SHA-256, and logs metadata.

        Security Enforcement:
          - Database name validated against strict identifier regex.
          - No password on command line: passed strictly via MYSQL_PWD environment variable.
          - mysqldump uses --single-transaction, --routines, --triggers, --events, --quick.
          - Permissions restricted on host.
        """
        valid_db = validate_sql_identifier(database_name)
        timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d_%H%M%S")
        unique_token = str(uuid.uuid4())[:8]
        filename = f"backup_{valid_db}_{timestamp}_{unique_token}.sql.gz"
        dest_path = self.backup_dir / filename

        logger.info(
            "Initiating logical backup for database '%s' into '%s'...",
            valid_db,
            dest_path.name,
        )

        # 1. Prepare mysqldump command and isolated environment (no password on CLI)
        dump_cmd = [
            "mysqldump",
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            "--quick",
            "-u",
            config.user,
            valid_db,
        ]
        dump_env = {"MYSQL_PWD": config.password}

        # 2. Execute via DockerManager if available
        sql_content: bytes = b""
        if self.docker_manager:
            exit_code, output = self.docker_manager.exec_in_container(
                container_name_or_id=container_name,
                command=dump_cmd,
                environment=dump_env,
            )
            if exit_code != 0:
                sanitized_err = sanitize_error_message(output)
                raise DumpProcessFailedError(
                    f"mysqldump exited with error code {exit_code}: {sanitized_err}"
                )
            sql_content = output.encode("utf-8") if isinstance(output, str) else output
        else:
            # Fallback for environments where mysqldump runs via mock or direct synthesis
            raise DumpProcessFailedError(
                "DockerManager is required to execute in-container mysqldump."
            )

        # 3. Validate content completeness and SQL markers
        if not sql_content or len(sql_content.strip()) == 0:
            raise BackupTruncatedError(
                f"mysqldump produced empty output for database '{valid_db}'."
            )

        sql_text = sql_content.decode("utf-8", errors="replace")
        has_dump_header = (
            "-- MySQL dump" in sql_text or "/*!40101 SET" in sql_text or "CREATE TABLE" in sql_text
        )
        if not has_dump_header:
            raise BackupCorruptError("Dump output is missing standard MySQL dump header markers.")

        if "-- Dump completed" not in sql_text and not sql_text.rstrip().endswith(";"):
            raise BackupTruncatedError(
                "Dump output appears truncated (missing completion marker or closing semicolon)."
            )

        # 4. Stream into gzip file and compute SHA-256 simultaneously
        try:
            with (
                open(dest_path, "wb") as f_out,
                gzip.GzipFile(filename=filename, mode="wb", fileobj=f_out, mtime=0.0) as gz_out,
            ):
                gz_out.write(sql_content)

            file_size = dest_path.stat().st_size
            sha256_checksum = compute_file_sha256(dest_path)
        except OSError as err:
            if "No space left on device" in str(err) or (
                hasattr(err, "winerror") and err.winerror == 112
            ):
                raise BackupStorageFullError(
                    "Host storage full while writing backup archive."
                ) from err
            raise BackupError(
                f"Failed writing compressed backup archive: {sanitize_error_message(err)}"
            ) from err

        # 5. Restrict permissions
        restrict_file_permissions(dest_path)

        # 6. Record metadata in SQLite
        metadata_json = json.dumps(table_row_counts or {})
        backup_id = self.audit_logger.record_backup(
            filename=filename,
            file_path=str(dest_path),
            file_size_bytes=file_size,
            sha256_checksum=sha256_checksum,
            compression_type="GZIP",
            database_name=valid_db,
            total_tables=total_tables,
            total_rows=total_rows,
            status="CREATED",
            run_id=run_id,
            table_fingerprint=table_fingerprint,
            metadata_json=metadata_json,
        )

        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        logger.info(
            "Backup successfully generated [ID=%d, Size=%d bytes, SHA256=%s...]",
            backup_id,
            file_size,
            sha256_checksum[:12],
        )

        return BackupMetadata(
            backup_id=backup_id,
            run_id=run_id,
            filename=filename,
            file_path=dest_path,
            file_size_bytes=file_size,
            sha256_checksum=sha256_checksum,
            compression_type="GZIP",
            database_name=valid_db,
            total_tables=total_tables,
            total_rows=total_rows,
            status="CREATED",
            created_at=now_utc,
            table_fingerprint=table_fingerprint,
            table_row_counts=table_row_counts or {},
        )

    def verify_backup(
        self,
        backup_id: int | None = None,
        backup_path: Path | str | None = None,
        expected_sha256: str | None = None,
    ) -> bool:
        """Cryptographically verifies backup archive integrity and marks status as VERIFIED_FILE.

        Steps:
          1. File existence and non-zero size check.
          2. Recompute and match SHA-256 checksum.
          3. Validate gzip decompression.
          4. Check expected SQL markers (-- MySQL dump, Dump completed, etc.).
          5. Update status in SQLite to 'VERIFIED_FILE' (never 'restorable' yet).

        Raises:
            BackupChecksumMismatchError: If file hash does not match recorded checksum.
            BackupCorruptError: If gzip is corrupt or SQL syntax is invalid.
            BackupTruncatedError: If file is incomplete.
        """
        # Resolve target file path and expected hash
        record = None
        if backup_id is not None:
            record = self.audit_logger.get_backup(backup_id)
            if not record:
                raise BackupError(f"Backup record ID {backup_id} not found in audit store.")
            resolved_path = Path(record["file_path"]).resolve()
            expected_hash = record["sha256_checksum"]
        elif backup_path is not None:
            resolved_path = Path(backup_path).resolve()
            expected_hash = expected_sha256
        else:
            raise BackupError("Either backup_id or backup_path must be provided to verify_backup.")

        if not resolved_path.exists():
            raise BackupError(f"Backup archive does not exist: {resolved_path}")

        file_size = resolved_path.stat().st_size
        if file_size == 0:
            if backup_id is not None:
                self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise BackupTruncatedError(f"Backup archive '{resolved_path.name}' is empty (0 bytes).")

        # Step 1: Recompute SHA-256
        recomputed_hash = compute_file_sha256(resolved_path)
        if expected_hash and recomputed_hash.lower() != expected_hash.lower():
            if backup_id is not None:
                self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise BackupChecksumMismatchError(
                f"Checksum Mismatch for '{resolved_path.name}': "
                f"expected {expected_hash}, recomputed {recomputed_hash}."
            )

        # Step 2: Validate GZIP decompression & SQL dump markers
        try:
            with gzip.open(resolved_path, "rt", encoding="utf-8", errors="replace") as gz_in:
                content = gz_in.read()
        except (gzip.BadGzipFile, OSError) as err:
            if backup_id is not None:
                self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise BackupCorruptError(
                f"Backup archive '{resolved_path.name}' failed GZIP decompression: {err}"
            ) from err

        if not content.strip():
            if backup_id is not None:
                self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise BackupTruncatedError(f"Decompressed dump '{resolved_path.name}' is empty.")

        has_markers = (
            "-- MySQL dump" in content or "/*!40101 SET" in content or "CREATE TABLE" in content
        )
        if not has_markers:
            if backup_id is not None:
                self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise BackupCorruptError(
                f"Backup '{resolved_path.name}' is missing standard mysqldump markers."
            )

        has_completion = "-- Dump completed" in content or content.rstrip().endswith(";")
        if not has_completion:
            if backup_id is not None:
                self.audit_logger.update_backup_status(backup_id, "CORRUPT")
            raise BackupTruncatedError(
                f"Backup '{resolved_path.name}' appears truncated (missing completion signature)."
            )

        # Step 3: Mark verified
        if backup_id is not None:
            self.audit_logger.update_backup_status(backup_id, "VERIFIED_FILE")
            logger.info("Backup ID %d verified successfully (status: VERIFIED_FILE).", backup_id)

        return True
