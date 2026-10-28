"""Audit logging subsystem and SQLite operational metadata store.

Maintains an immutable, append-only operational audit trail in SQLite using WAL mode,
with strict parameterization, foreign keys, and JSON/CSV forensic export capabilities.
"""

from __future__ import annotations

import csv
import datetime
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbrescue.config import RescueException
from dbrescue.security import sanitize_error_message, validate_run_uuid

logger = logging.getLogger("dbrescue.audit")


# --- Exceptions ---

class AuditError(RescueException):
    """Base exception for audit store failures."""
    pass


class AuditLogWriteError(AuditError):
    """Raised when appending to SQLite audit trail fails."""
    pass


# --- Dataclasses ---

@dataclass(frozen=True)
class AuditEvent:
    """Represents a single immutable operational audit event."""
    event_id: int
    run_id: int
    stage: str
    event_type: str
    severity: str
    message: str
    duration_ms: float
    details: dict[str, Any]
    created_at: str


# --- Audit Store & Logger ---

class AuditLogger:
    """Manages SQLite operational metadata tables and appends audit events."""

    def __init__(self, sqlite_db_path: Path | str) -> None:
        self.sqlite_db_path = Path(sqlite_db_path).resolve()
        self.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.sqlite_db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_database(self) -> None:
        """Creates all 9 operational tables and performance indices as specified in CONTRACTS.md."""
        with self._get_connection() as conn:
            # 1. runs
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_uuid TEXT NOT NULL UNIQUE,
                    trigger_type TEXT NOT NULL,
                    target_volume TEXT NOT NULL,
                    primary_container TEXT NOT NULL,
                    sandbox_container TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    initial_stage TEXT NOT NULL,
                    final_stage TEXT,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 2. snapshots
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    captured_at TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    total_tables INTEGER NOT NULL,
                    total_rows INTEGER NOT NULL,
                    total_data_bytes INTEGER NOT NULL,
                    fingerprint_hash TEXT NOT NULL,
                    is_baseline BOOLEAN NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 3. snapshot_rows
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshot_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                    table_name TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    data_bytes INTEGER NOT NULL,
                    table_checksum TEXT NOT NULL,
                    check_table_status TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 4. backups
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
                    filename TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL,
                    sha256_checksum TEXT NOT NULL,
                    compression_type TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    total_tables INTEGER NOT NULL,
                    total_rows INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 5. restores
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS restores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    backup_id INTEGER NOT NULL REFERENCES backups(id) ON DELETE RESTRICT,
                    target_database TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    rows_restored INTEGER DEFAULT 0,
                    verification_status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 6. audit_events
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    duration_ms REAL DEFAULT 0.0,
                    details_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 7. alerts
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
                    alert_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    is_resolved BOOLEAN NOT NULL DEFAULT 0,
                    resolved_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 8. settings
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    description TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 9. metrics_samples
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
                    timestamp TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    labels_json TEXT
                );
                """
            )

            # Indices
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_uuid ON runs(run_uuid);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events(run_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_stage ON audit_events(stage);")
            conn.commit()

    def create_run(
        self,
        run_uuid: str,
        target_volume: str,
        primary_container: str,
        trigger_type: str = "MANUAL_CLI",
        initial_stage: str = "INIT_RUN",
    ) -> int:
        """Registers a new disaster recovery run in SQLite. Returns database run_id."""
        valid_uuid = validate_run_uuid(run_uuid)
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO runs (
                        run_uuid, trigger_type, target_volume, primary_container,
                        started_at, status, current_stage, initial_stage
                    ) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?);
                    """,
                    (
                        valid_uuid,
                        trigger_type,
                        target_volume,
                        primary_container,
                        now_utc,
                        initial_stage,
                        initial_stage,
                    ),
                )
                conn.commit()
                run_id = cursor.lastrowid
                assert run_id is not None
                return run_id
        except sqlite3.Error as err:
            raise AuditLogWriteError(f"Failed to create run: {sanitize_error_message(err)}") from err

    def update_run_stage(self, run_id: int, current_stage: str) -> None:
        """Updates the current stage of an active run."""
        with self._get_connection() as conn:
            conn.execute("UPDATE runs SET current_stage = ? WHERE id = ?;", (current_stage, run_id))
            conn.commit()

    def complete_run(
        self,
        run_id: int,
        status: str,
        final_stage: str,
        error_message: str | None = None
    ) -> None:
        """Marks run as completed with terminal status and sanitized error if applicable."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        sanitized_err = sanitize_error_message(error_message) if error_message else None

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE runs SET
                    status = ?,
                    final_stage = ?,
                    current_stage = ?,
                    completed_at = ?,
                    error_message = ?
                WHERE id = ?;
                """,
                (status, final_stage, final_stage, now_utc, sanitized_err, run_id),
            )
            conn.commit()

    def log_event(
        self,
        run_id: int,
        stage: str,
        event_type: str,
        severity: str,
        message: str,
        duration_ms: float = 0.0,
        details: dict[str, Any] | None = None
    ) -> AuditEvent:
        """Appends an immutable audit event record into SQLite."""
        sanitized_msg = sanitize_error_message(message)
        details_json = json.dumps(details or {})
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()

        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO audit_events (
                        run_id, stage, event_type, severity, message, duration_ms, details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (run_id, stage, event_type, severity, sanitized_msg, duration_ms, details_json, now_utc),
                )
                conn.commit()
                event_id = cursor.lastrowid
                assert event_id is not None

                return AuditEvent(
                    event_id=event_id,
                    run_id=run_id,
                    stage=stage,
                    event_type=event_type,
                    severity=severity,
                    message=sanitized_msg,
                    duration_ms=duration_ms,
                    details=details or {},
                    created_at=now_utc,
                )
        except sqlite3.Error as err:
            raise AuditLogWriteError(f"Failed to record audit event: {sanitize_error_message(err)}") from err

    def get_run_audit_trail(self, run_id: int) -> list[AuditEvent]:
        """Retrieves ordered sequence of audit events for a given run."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, run_id, stage, event_type, severity, message, duration_ms, details_json, created_at
                FROM audit_events
                WHERE run_id = ?
                ORDER BY id ASC;
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
            return [
                AuditEvent(
                    event_id=row["id"],
                    run_id=row["run_id"],
                    stage=row["stage"],
                    event_type=row["event_type"],
                    severity=row["severity"],
                    message=row["message"],
                    duration_ms=float(row["duration_ms"] or 0.0),
                    details=json.loads(row["details_json"]) if row["details_json"] else {},
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    def export_audit_log_json(self, run_id: int, output_path: Path | str) -> Path:
        """Exports audit records to a structured JSON file."""
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        events = self.get_run_audit_trail(run_id)
        data = [
            {
                "event_id": e.event_id,
                "run_id": e.run_id,
                "stage": e.stage,
                "event_type": e.event_type,
                "severity": e.severity,
                "message": e.message,
                "duration_ms": e.duration_ms,
                "details": e.details,
                "created_at": e.created_at,
            }
            for e in events
        ]

        with open(dest, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return dest

    def export_run_csv(self, run_id: int, output_path: Path | str) -> Path:
        """Exports run audit trail to tabular CSV format."""
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        events = self.get_run_audit_trail(run_id)
        fieldnames = ["event_id", "run_id", "stage", "event_type", "severity", "message", "duration_ms", "created_at"]

        with open(dest, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for e in events:
                writer.writerow(
                    {
                        "event_id": e.event_id,
                        "run_id": e.run_id,
                        "stage": e.stage,
                        "event_type": e.event_type,
                        "severity": e.severity,
                        "message": e.message,
                        "duration_ms": e.duration_ms,
                        "created_at": e.created_at,
                    }
                )

        return dest

    def record_snapshot(
        self,
        run_id: int,
        database_name: str,
        total_tables: int,
        total_rows: int,
        total_data_bytes: int,
        fingerprint_hash: str,
        is_baseline: bool = True,
    ) -> int:
        """Stores a snapshot header record for a run."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshots (
                    run_id, captured_at, database_name, total_tables, total_rows,
                    total_data_bytes, fingerprint_hash, is_baseline
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    now_utc,
                    database_name,
                    total_tables,
                    total_rows,
                    total_data_bytes,
                    fingerprint_hash,
                    1 if is_baseline else 0,
                ),
            )
            conn.commit()
            snap_id = cur.lastrowid
            assert snap_id is not None
            return snap_id

    def record_snapshot_table(
        self,
        snapshot_id: int,
        table_name: str,
        row_count: int,
        data_bytes: int,
        table_checksum: str,
        check_table_status: str,
    ) -> int:
        """Stores per-table checksum and row-count metadata linked to a snapshot."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshot_rows (
                    snapshot_id, table_name, row_count, data_bytes,
                    table_checksum, check_table_status
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot_id,
                    table_name,
                    row_count,
                    data_bytes,
                    table_checksum,
                    check_table_status,
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
            assert row_id is not None
            return row_id

    def record_alert(
        self,
        alert_name: str,
        severity: str,
        message: str,
        run_id: int | None = None,
    ) -> int:
        """Persists an operational alert into SQLite."""
        sanitized_msg = sanitize_error_message(message)
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO alerts (run_id, alert_name, severity, message, is_resolved)
                VALUES (?, ?, ?, ?, 0);
                """,
                (run_id, alert_name, severity, sanitized_msg),
            )
            conn.commit()
            alert_id = cur.lastrowid
            assert alert_id is not None
            return alert_id

    def resolve_alert(self, alert_id: int) -> bool:
        """Marks an active alert as resolved."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        with self._get_connection() as conn:
            cur = conn.execute(
                "UPDATE alerts SET is_resolved = 1, resolved_at = ? WHERE id = ?;",
                (now_utc, alert_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def set_setting(self, key: str, value: str, description: str | None = None) -> None:
        """Inserts or updates a persistent setting."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, description)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    description = COALESCE(excluded.description, settings.description),
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (key, value, description),
            )
            conn.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Retrieves a configuration setting by key."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT value FROM settings WHERE key = ?;", (key,))
            row = cur.fetchone()
            if row:
                return str(row["value"])
            return default
