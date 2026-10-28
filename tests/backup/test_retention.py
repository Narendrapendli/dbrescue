"""Unit tests for backup retention policies, dry-run mode, confirmation tokens, and verified backup preservation."""

import datetime
from pathlib import Path

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.retention import (
    RetentionConfirmationRequiredError,
    RetentionManager,
    RetentionPolicy,
)


@pytest.fixture
def retention_env(tmp_path: Path) -> dict:
    sqlite_path = tmp_path / "retention_test.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    audit_logger = AuditLogger(sqlite_path)
    manager = RetentionManager(
        sqlite_db_path=sqlite_path,
        backup_dir=backup_dir,
        audit_logger=audit_logger,
    )

    return {
        "manager": manager,
        "audit_logger": audit_logger,
        "backup_dir": backup_dir,
    }


def _seed_backup(
    audit_logger: AuditLogger,
    backup_dir: Path,
    filename: str,
    days_ago: int,
    status: str = "CREATED",
) -> int:
    file_path = backup_dir / filename
    file_path.write_text("DUMMY_BACKUP_CONTENT", encoding="utf-8")

    dt = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_ago)
    created_str = dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = audit_logger._get_connection()
    cur = conn.execute(
        """
        INSERT INTO backups (
            filename, file_path, file_size_bytes, sha256_checksum, compression_type,
            database_name, total_tables, total_rows, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            filename,
            str(file_path),
            file_path.stat().st_size,
            "dummy_sha256",
            "GZIP",
            "student_db",
            1,
            10,
            status,
            created_str,
        ),
    )
    conn.commit()
    backup_id = cur.lastrowid
    conn.close()
    assert backup_id is not None
    return backup_id


def test_retention_requires_confirmation(retention_env: dict) -> None:
    manager: RetentionManager = retention_env["manager"]

    policy = RetentionPolicy(keep_last_n=1, max_age_days=1, require_confirmation=True)
    with pytest.raises(RetentionConfirmationRequiredError) as exc_info:
        manager.apply_retention_policy(policy=policy, dry_run=False, confirm=False)
    assert "confirm=True" in str(exc_info.value)


def test_retention_dry_run_mode(retention_env: dict) -> None:
    manager: RetentionManager = retention_env["manager"]
    audit_logger: AuditLogger = retention_env["audit_logger"]
    backup_dir: Path = retention_env["backup_dir"]

    # Create 3 old backups
    _seed_backup(audit_logger, backup_dir, "b1.sql.gz", days_ago=45)
    id2 = _seed_backup(audit_logger, backup_dir, "b2.sql.gz", days_ago=50)
    _seed_backup(audit_logger, backup_dir, "b3.sql.gz", days_ago=55)

    policy = RetentionPolicy(keep_last_n=1, max_age_days=30)
    result = manager.apply_retention_policy(policy=policy, dry_run=True)

    assert result.is_dry_run is True
    assert result.deleted_count == 2
    assert result.preserved_count == 1

    # Files must NOT be deleted from disk in dry run
    assert (backup_dir / "b2.sql.gz").exists()
    assert (backup_dir / "b3.sql.gz").exists()

    # SQLite status must NOT be EXPIRED in dry run
    b2 = audit_logger.get_backup(id2)
    assert b2 is not None
    assert b2["status"] != "EXPIRED"


def test_retention_keeps_newest_verified(retention_env: dict) -> None:
    """CRITICAL SAFETY GUARD: The newest verified backup is NEVER deleted,

    even if it exceeds max_age_days and keep_last_n.
    """
    manager: RetentionManager = retention_env["manager"]
    audit_logger: AuditLogger = retention_env["audit_logger"]
    backup_dir: Path = retention_env["backup_dir"]

    # Backup 1: 60 days old, but VERIFIED_FILE (the only verified backup)
    verified_id = _seed_backup(
        audit_logger, backup_dir, "verified_old.sql.gz", days_ago=60, status="VERIFIED_FILE"
    )

    # Backup 2: 40 days old, CREATED
    b2_id = _seed_backup(
        audit_logger, backup_dir, "b2_unverified.sql.gz", days_ago=40, status="CREATED"
    )

    # Backup 3: 5 days old, CREATED
    _seed_backup(
        audit_logger, backup_dir, "b3_recent.sql.gz", days_ago=5, status="CREATED"
    )

    # Policy: keep last 1, max age 10 days
    # Without the safety guard, verified_old would be purged (index 2 >= 1, age 60 > 10).
    policy = RetentionPolicy(keep_last_n=1, max_age_days=10)
    result = manager.apply_retention_policy(policy=policy, dry_run=False, confirm=True)

    # verified_old must be preserved!
    assert verified_id not in result.deleted_backup_ids
    assert result.preserved_count >= 1
    assert (backup_dir / "verified_old.sql.gz").exists()

    # b2_unverified is old and unverified -> must be deleted
    assert b2_id in result.deleted_backup_ids
    assert not (backup_dir / "b2_unverified.sql.gz").exists()

    # SQLite status of verified backup must remain VERIFIED_FILE
    v_rec = audit_logger.get_backup(verified_id)
    assert v_rec is not None
    assert v_rec["status"] == "VERIFIED_FILE"


def test_retention_purges_older_unverified(retention_env: dict) -> None:
    manager: RetentionManager = retention_env["manager"]
    audit_logger: AuditLogger = retention_env["audit_logger"]
    backup_dir: Path = retention_env["backup_dir"]

    # 3 backups: 1 recent, 2 old
    _seed_backup(audit_logger, backup_dir, "recent.sql.gz", days_ago=2)
    old1_id = _seed_backup(audit_logger, backup_dir, "old1.sql.gz", days_ago=45)
    old2_id = _seed_backup(audit_logger, backup_dir, "old2.sql.gz", days_ago=60)

    policy = RetentionPolicy(keep_last_n=1, max_age_days=30)
    result = manager.apply_retention_policy(policy=policy, dry_run=False, confirm=True)

    assert result.deleted_count == 2
    assert result.preserved_count == 1
    assert old1_id in result.deleted_backup_ids
    assert old2_id in result.deleted_backup_ids

    # Files removed from disk
    assert not (backup_dir / "old1.sql.gz").exists()
    assert not (backup_dir / "old2.sql.gz").exists()
    assert (backup_dir / "recent.sql.gz").exists()

    # Marked EXPIRED in SQLite
    rec1 = audit_logger.get_backup(old1_id)
    assert rec1 is not None
    assert rec1["status"] == "EXPIRED"
