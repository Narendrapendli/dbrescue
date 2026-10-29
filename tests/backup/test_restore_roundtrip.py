"""Live integration test for end-to-end backup creation, cryptographic verification, and restore roundtrip parity."""

from pathlib import Path

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.backup import BackupManager
from dbrescue.config import MySQLConnectionConfig, Status
from dbrescue.db import ping_database
from dbrescue.docker_manager import DockerManager
from dbrescue.integrity import IntegrityEngine
from dbrescue.restore import RestoreEngine


@pytest.mark.integration
def test_live_backup_restore_roundtrip_parity(tmp_path: Path) -> None:
    """End-to-end integration test against live Docker Compose MySQL stack.

    If MySQL container is not running on 127.0.0.1:3306, skips cleanly.
    """
    backup_cfg = MySQLConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="rescue_backup",
        password="RescueBackup2026!Safe",
        database="student_db",
        root_password="RootRescue2026!Emergency",
    )

    restore_cfg = MySQLConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="rescue_restore",
        password="RescueRestore2026!DryRun",
        database="student_db",
        root_password="RootRescue2026!Emergency",
    )

    if not ping_database(backup_cfg, timeout_sec=1):
        pytest.skip(
            "MySQL container is not running on 127.0.0.1:3306. Skipping live restore roundtrip test."
        )

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = tmp_path / "roundtrip_audit.sqlite3"

    audit_logger = AuditLogger(sqlite_path)
    docker_mgr = DockerManager()
    integrity_eng = IntegrityEngine(sqlite_path)

    backup_mgr = BackupManager(
        backup_dir=backup_dir,
        sqlite_db_path=sqlite_path,
        docker_manager=docker_mgr,
        integrity_engine=integrity_eng,
        audit_logger=audit_logger,
    )

    restore_engine = RestoreEngine(
        sqlite_db_path=str(sqlite_path),
        docker_manager=docker_mgr,
        integrity_engine=integrity_eng,
        audit_logger=audit_logger,
    )

    # 1. Create logical backup of student_db
    meta = backup_mgr.create_logical_backup(
        config=backup_cfg,
        database_name="student_db",
        container_name="dbrescue_mysql",
    )
    assert meta.backup_id > 0
    assert meta.file_path.exists()

    # 2. Cryptographic file verification
    verified = backup_mgr.verify_backup(backup_id=meta.backup_id)
    assert verified is True

    # 3. Isolated restore into dbrescue_restore_<id> with restricted user
    result = restore_engine.execute_isolated_restore(
        backup_id=meta.backup_id,
        target_config=restore_cfg,
    )

    assert result.status == Status.SUCCESS
    assert result.verification_status == "VERIFIED"
    assert result.rows_restored > 0

    # 4. Assert backup marked RESTORE_VERIFIED
    updated_meta = audit_logger.get_backup(meta.backup_id)
    assert updated_meta is not None
    assert updated_meta["status"] == "RESTORE_VERIFIED"
