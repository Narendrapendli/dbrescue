"""Data integrity auditing, canonical serialization, and cryptographic fingerprinting.

Implements deterministic primary-key ordered canonical serialization with Unicode NFC
normalization, type-tagged length prefixing, row-level SHA-256 hashing, composite dataset
fingerprinting, and granular column-level discrepancy comparison.
"""

from __future__ import annotations

import datetime
import hashlib
import sqlite3
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dbrescue.config import RescueException
from dbrescue.db import MySQLConnectionPool, execute_query, get_table_names
from dbrescue.security import validate_sql_identifier

# --- Exceptions ---

class IntegrityError(RescueException):
    """Base exception for all integrity auditing and fingerprinting failures."""
    pass


class TableCorruptionError(IntegrityError):
    """Raised when MySQL CHECK TABLE reports errors or structural corruption."""
    pass


class SnapshotNotFoundError(IntegrityError):
    """Raised when a requested snapshot record is absent in SQLite."""
    pass


class SchemaMismatchError(IntegrityError):
    """Raised when comparing snapshots with mismatched table definitions."""
    pass


# --- Dataclasses ---

@dataclass(frozen=True)
class TableAuditRecord:
    """Audit result for an individual database table."""
    table_name: str
    row_count: int
    data_bytes: int
    table_checksum: str
    check_status: str  # 'OK', 'WARNING', 'CORRUPT'
    error_message: str | None = None


@dataclass(frozen=True)
class IntegrityReport:
    """Full database-level integrity audit report."""
    database_name: str
    audited_at: str
    total_tables: int
    total_rows: int
    total_bytes: int
    fingerprint_hash: str
    table_records: list[TableAuditRecord]
    passed: bool


@dataclass(frozen=True)
class TableComparisonResult:
    """Granular comparison result between two states of a database table."""
    table_name: str
    baseline_count: int
    target_count: int
    count_match: bool
    baseline_fingerprint: str
    target_fingerprint: str
    fingerprint_match: bool
    missing_ids: list[Any]
    unexpected_ids: list[Any]
    changed_ids: dict[Any, dict[str, tuple[Any, Any]]]
    reasons: list[str]
    passed: bool


# --- Canonical Serialization Engine ---

def serialize_value(val: Any) -> bytes:
    """Serializes an individual field value into an unambiguous, length-prefixed byte stream.

    Format: <TypeTag>:<LengthInBytes>:<EncodedBytes>
    Guarantees no delimiter collisions (e.g. ('a', 'bc') vs ('ab', 'c')).
    """
    if val is None:
        return b"N:0:"

    if isinstance(val, bool):
        payload = b"1" if val else b"0"
        return b"B:1:" + payload

    if isinstance(val, int):
        payload = str(val).encode("utf-8")
        return b"I:" + str(len(payload)).encode("ascii") + b":" + payload

    if isinstance(val, Decimal):
        # Normalize decimal representation: strip unnecessary trailing zeros but keep standard decimal
        normalized = val.normalize()
        # Handle cases where exponent is zero
        formatted = f"{normalized:f}" if normalized.as_tuple().exponent <= 0 else str(normalized)
        payload = formatted.encode("utf-8")
        return b"D:" + str(len(payload)).encode("ascii") + b":" + payload

    if isinstance(val, float):
        payload = f"{val:.10g}".encode()
        return b"F:" + str(len(payload)).encode("ascii") + b":" + payload

    if isinstance(val, datetime.datetime | datetime.date | datetime.time):
        # Convert to standard ISO-8601 string
        iso_str = val.isoformat()
        payload = iso_str.encode("utf-8")
        return b"T:" + str(len(payload)).encode("ascii") + b":" + payload

    if isinstance(val, str):
        # Unicode NFC Normalization (canonical decomposition followed by canonical composition)
        nfc_normalized = unicodedata.normalize("NFC", val)
        payload = nfc_normalized.encode("utf-8")
        return b"S:" + str(len(payload)).encode("ascii") + b":" + payload

    if isinstance(val, bytes | bytearray):
        payload = bytes(val)
        return b"Y:" + str(len(payload)).encode("ascii") + b":" + payload

    # Fallback to string representation with NFC normalization
    payload = unicodedata.normalize("NFC", str(val)).encode("utf-8")
    return b"O:" + str(len(payload)).encode("ascii") + b":" + payload


def serialize_row(columns: Sequence[str], row: Sequence[Any]) -> bytes:
    """Serializes a single database row into a canonical byte sequence.

    Encodes fixed column names and length-prefixed field values in strict order.
    """
    parts: list[bytes] = []
    for col_name, val in zip(columns, row, strict=False):
        col_bytes = col_name.encode("utf-8")
        parts.append(b"C:" + str(len(col_bytes)).encode("ascii") + b":" + col_bytes)
        parts.append(serialize_value(val))
    return b"".join(parts)


def compute_row_hash(columns: Sequence[str], row: Sequence[Any]) -> str:
    """Calculates the SHA-256 hex digest of a canonicalized database row."""
    serialized = serialize_row(columns, row)
    return hashlib.sha256(serialized).hexdigest()


def compute_table_fingerprint_from_rows(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    primary_key_indices: Sequence[int] = (0,),
) -> tuple[str, dict[Any, str]]:
    """Sorts rows deterministically by primary key and computes overall table SHA-256 fingerprint.

    Returns:
        (overall_fingerprint_hash, dict_mapping_pk_to_row_hash)
    """
    if not rows:
        empty_hash = hashlib.sha256(b"EMPTY_DATASET_V1").hexdigest()
        return empty_hash, {}

    # Sort rows by primary key tuple for ordering independence
    def _extract_pk(r: Sequence[Any]) -> tuple[Any, ...]:
        return tuple(r[idx] for idx in primary_key_indices)

    sorted_rows = sorted(rows, key=_extract_pk)

    hasher = hashlib.sha256()
    row_hashes: dict[Any, str] = {}

    for r in sorted_rows:
        pk = _extract_pk(r)
        pk_key = pk[0] if len(pk) == 1 else pk
        h = compute_row_hash(columns, r)
        row_hashes[pk_key] = h
        hasher.update(h.encode("ascii"))

    overall_fingerprint = hasher.hexdigest()
    return overall_fingerprint, row_hashes


# --- Dataset Comparison Engine ---

def compare_datasets(
    table_name: str,
    columns: Sequence[str],
    baseline_rows: Sequence[Sequence[Any]],
    target_rows: Sequence[Sequence[Any]],
    primary_key_indices: Sequence[int] = (0,),
) -> TableComparisonResult:
    """Performs deep comparison between baseline and target row collections.

    Detects missing IDs, unexpected IDs, and granular column-level diffs for changed rows.
    """
    baseline_count = len(baseline_rows)
    target_count = len(target_rows)
    count_match = baseline_count == target_count

    baseline_fp, baseline_row_hashes = compute_table_fingerprint_from_rows(columns, baseline_rows, primary_key_indices)
    target_fp, target_row_hashes = compute_table_fingerprint_from_rows(columns, target_rows, primary_key_indices)
    fingerprint_match = baseline_fp == target_fp

    def _map_by_pk(rows: Sequence[Sequence[Any]]) -> dict[Any, Sequence[Any]]:
        res: dict[Any, Sequence[Any]] = {}
        for r in rows:
            pk = tuple(r[idx] for idx in primary_key_indices)
            key = pk[0] if len(pk) == 1 else pk
            res[key] = r
        return res

    baseline_dict = _map_by_pk(baseline_rows)
    target_dict = _map_by_pk(target_rows)

    baseline_keys = set(baseline_dict.keys())
    target_keys = set(target_dict.keys())

    missing_ids = sorted(baseline_keys - target_keys, key=str)
    unexpected_ids = sorted(target_keys - baseline_keys, key=str)

    # Detect changed rows and compute column diffs
    common_keys = baseline_keys & target_keys
    changed_ids: dict[Any, dict[str, tuple[Any, Any]]] = {}

    for k in sorted(common_keys, key=str):
        b_hash = baseline_row_hashes[k]
        t_hash = target_row_hashes[k]
        if b_hash != t_hash:
            b_row = baseline_dict[k]
            t_row = target_dict[k]
            col_diffs: dict[str, tuple[Any, Any]] = {}
            for col, b_val, t_val in zip(columns, b_row, t_row, strict=False):
                if serialize_value(b_val) != serialize_value(t_val):
                    col_diffs[col] = (b_val, t_val)
            changed_ids[k] = col_diffs

    reasons: list[str] = []
    if not count_match:
        reasons.append(f"Row count mismatch: baseline={baseline_count}, target={target_count}")
    if not fingerprint_match:
        reasons.append(f"Dataset fingerprint mismatch: baseline={baseline_fp}, target={target_fp}")
    if missing_ids:
        reasons.append(f"Found {len(missing_ids)} missing records in target (e.g. {missing_ids[:3]})")
    if unexpected_ids:
        reasons.append(f"Found {len(unexpected_ids)} unexpected records in target (e.g. {unexpected_ids[:3]})")
    if changed_ids:
        reasons.append(f"Found {len(changed_ids)} modified records with column-level value discrepancies")

    passed = (
        count_match
        and fingerprint_match
        and len(missing_ids) == 0
        and len(unexpected_ids) == 0
        and len(changed_ids) == 0
    )

    return TableComparisonResult(
        table_name=table_name,
        baseline_count=baseline_count,
        target_count=target_count,
        count_match=count_match,
        baseline_fingerprint=baseline_fp,
        target_fingerprint=target_fp,
        fingerprint_match=fingerprint_match,
        missing_ids=missing_ids,
        unexpected_ids=unexpected_ids,
        changed_ids=changed_ids,
        reasons=reasons,
        passed=passed,
    )


# --- SQLite Persistent Snapshot & Audit Engine ---

class IntegrityEngine:
    """Manages database audits, table integrity verification, and SQLite snapshot persistence."""

    def __init__(self, sqlite_db_path: Path) -> None:
        self.sqlite_db_path = sqlite_db_path
        self._init_sqlite_tables()

    def _init_sqlite_tables(self) -> None:
        self.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.sqlite_db_path) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
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
            conn.commit()

    def execute_check_table(self, pool: MySQLConnectionPool, database_name: str, table_name: str) -> TableAuditRecord:
        """Executes CHECK TABLE ... EXTENDED against a table and returns TableAuditRecord."""
        valid_db = validate_sql_identifier(database_name, "database_name")
        valid_tbl = validate_sql_identifier(table_name, "table_name")

        # 1. Run CHECK TABLE ... EXTENDED
        check_sql = f"CHECK TABLE `{valid_db}`.`{valid_tbl}` EXTENDED"
        res_check = execute_query(pool, check_sql)

        check_status = "OK"
        error_msg: str | None = None

        for row in res_check.rows:
            # row: (Table, Op, Msg_type, Msg_text)
            msg_type = str(row[2]).lower()
            msg_text = str(row[3])
            if msg_type in ("error", "fatal"):
                check_status = "CORRUPT"
                error_msg = msg_text
            elif msg_type == "warning" and check_status != "CORRUPT":
                check_status = "WARNING"
                error_msg = msg_text

        # 2. Get table stats from information_schema
        stats_sql = """
            SELECT table_rows, data_length
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        """
        res_stats = execute_query(pool, stats_sql, (valid_db, valid_tbl))
        row_count = int(res_stats.rows[0][0]) if res_stats.rows and res_stats.rows[0][0] is not None else 0
        data_bytes = int(res_stats.rows[0][1]) if res_stats.rows and res_stats.rows[0][1] is not None else 0

        # 3. Get accurate row count if table_rows is approximate
        count_res = execute_query(pool, f"SELECT COUNT(*) FROM `{valid_db}`.`{valid_tbl}`")
        if count_res.rows:
            row_count = int(count_res.rows[0][0])

        # 4. Compute fingerprint
        fingerprint = self.compute_table_fingerprint(pool, valid_db, valid_tbl)

        return TableAuditRecord(
            table_name=valid_tbl,
            row_count=row_count,
            data_bytes=data_bytes,
            table_checksum=fingerprint,
            check_status=check_status,
            error_message=error_msg,
        )

    def compute_table_fingerprint(self, pool: MySQLConnectionPool, database_name: str, table_name: str) -> str:
        """Queries table primary keys, fetches all rows ordered by PK, and computes cryptographic fingerprint."""
        valid_db = validate_sql_identifier(database_name, "database_name")
        valid_tbl = validate_sql_identifier(table_name, "table_name")

        # Find primary key columns
        pk_sql = """
            SELECT column_name
            FROM information_schema.key_column_usage
            WHERE table_schema = %s AND table_name = %s AND constraint_name = 'PRIMARY'
            ORDER BY ordinal_position ASC;
        """
        res_pk = execute_query(pool, pk_sql, (valid_db, valid_tbl))
        pk_cols = [str(r[0]) for r in res_pk.rows]

        # Fetch all rows ordered
        if pk_cols:
            order_by = ", ".join(f"`{col}` ASC" for col in pk_cols)
            fetch_sql = f"SELECT * FROM `{valid_db}`.`{valid_tbl}` ORDER BY {order_by}"
        else:
            fetch_sql = f"SELECT * FROM `{valid_db}`.`{valid_tbl}`"

        res = execute_query(pool, fetch_sql)
        pk_indices = [res.columns.index(col) for col in pk_cols] if pk_cols else [0] if res.columns else [0]

        fingerprint, _ = compute_table_fingerprint_from_rows(res.columns, res.rows, pk_indices)
        return fingerprint

    def audit_database_integrity(self, pool: MySQLConnectionPool, database_name: str) -> IntegrityReport:
        """Audits all tables in database, validates structural checks, and calculates overall fingerprint."""
        valid_db = validate_sql_identifier(database_name, "database_name")
        tables = get_table_names(pool, valid_db)

        records: list[TableAuditRecord] = []
        composite_hasher = hashlib.sha256()
        total_rows = 0
        total_bytes = 0
        all_passed = True

        for tbl in tables:
            record = self.execute_check_table(pool, valid_db, tbl)
            records.append(record)
            total_rows += record.row_count
            total_bytes += record.data_bytes
            composite_hasher.update(record.table_checksum.encode("ascii"))
            if record.check_status == "CORRUPT":
                all_passed = False

        audited_at = datetime.datetime.now(datetime.UTC).isoformat()
        overall_fingerprint = composite_hasher.hexdigest()

        return IntegrityReport(
            database_name=valid_db,
            audited_at=audited_at,
            total_tables=len(tables),
            total_rows=total_rows,
            total_bytes=total_bytes,
            fingerprint_hash=overall_fingerprint,
            table_records=records,
            passed=all_passed,
        )

    def capture_database_snapshot(
        self,
        pool: MySQLConnectionPool,
        database_name: str,
        run_id: int,
        is_baseline: bool = False
    ) -> int:
        """Executes audit and persists snapshot header and per-table rows into SQLite."""
        report = self.audit_database_integrity(pool, database_name)

        with sqlite3.connect(self.sqlite_db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO snapshots (
                    run_id, captured_at, database_name, total_tables,
                    total_rows, total_data_bytes, fingerprint_hash, is_baseline
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    report.audited_at,
                    report.database_name,
                    report.total_tables,
                    report.total_rows,
                    report.total_bytes,
                    report.fingerprint_hash,
                    1 if is_baseline else 0,
                ),
            )
            snapshot_id = cursor.lastrowid
            assert snapshot_id is not None

            for rec in report.table_records:
                conn.execute(
                    """
                    INSERT INTO snapshot_rows (
                        snapshot_id, table_name, row_count, data_bytes,
                        table_checksum, check_table_status
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        snapshot_id,
                        rec.table_name,
                        rec.row_count,
                        rec.data_bytes,
                        rec.table_checksum,
                        rec.check_status,
                    ),
                )
            conn.commit()

        return snapshot_id
