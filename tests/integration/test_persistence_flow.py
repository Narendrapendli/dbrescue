"""Integration tests for end-to-end recovery persistence flow.

Verifies the full lifecycle:
- Acquires cross-platform file lock
- Validates pre-flight Docker & storage metadata
- Executes state transitions through the 18 steps
- Persists run metadata and audit events to SQLite (WAL mode)
- Validates terminal status and writes evidence JSON artifact
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.config import AppConfig, Status
from dbrescue.docker_manager import DockerManager
from dbrescue.integrity import IntegrityEngine
from dbrescue.recovery import RecoveryOrchestrator, RecoveryStage
from dbrescue.volume_inspector import VolumeInfo, VolumeInspector


@pytest.fixture
def persistence_env(tmp_path: Path) -> dict:
    sqlite_path = tmp_path / "persistence_audit.sqlite3"
    lock_file = tmp_path / "recovery.lock"
    evidence_dir = tmp_path / "evidence" / "runs"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    cfg = MagicMock(spec=AppConfig)
    cfg.sqlite_db_path = sqlite_path
    cfg.docker = MagicMock()
    cfg.docker.primary_container_name = "mysql_prod"
    cfg.docker.sandbox_container_name = "mysql_sandbox"
    cfg.docker.volume_name = "mysql_data"
    cfg.docker.image = "mysql:8.4.3"

    audit_logger = AuditLogger(sqlite_path)

    docker_mgr = MagicMock(spec=DockerManager)
    docker_mgr.is_docker_available.return_value = True

    vol_inspector = MagicMock(spec=VolumeInspector)
    vol_inspector.inspect_volume.return_value = VolumeInfo(
        name="mysql_data",
        driver="local",
        mountpoint="/var/lib/docker/volumes/mysql_data/_data",
        size_bytes=20971520,
        size_display="20.0 MB",
        labels={"com.docker.compose.project": "dbrescue"},
    )

    integrity_eng = MagicMock(spec=IntegrityEngine)

    orchestrator = RecoveryOrchestrator(
        config=cfg,
        docker_manager=docker_mgr,
        volume_inspector=vol_inspector,
        integrity_engine=integrity_eng,
        audit_logger=audit_logger,
    )

    return {
        "config": cfg,
        "sqlite_path": sqlite_path,
        "lock_file": lock_file,
        "evidence_dir": evidence_dir,
        "audit_logger": audit_logger,
        "orchestrator": orchestrator,
    }


def test_full_persistence_lifecycle_success(persistence_env: dict) -> None:
    orchestrator: RecoveryOrchestrator = persistence_env["orchestrator"]
    audit_logger: AuditLogger = persistence_env["audit_logger"]
    captured_progress: list[tuple[RecoveryStage, float, str]] = []

    def on_progress(stage: RecoveryStage, pct: float, msg: str) -> None:
        captured_progress.append((stage, pct, msg))

    # Execute full recovery flow with confirmation
    result = orchestrator.execute_recovery(
        target_volume="mysql_data",
        trigger="INTEGRATION_TEST",
        confirm_destructive=True,
        progress_cb=on_progress,
    )

    # 1. Assert result structure
    assert result.status == Status.SUCCESS
    assert result.passed is True
    assert result.final_stage == RecoveryStage.FINALIZE_RUN
    assert result.duration_seconds > 0.0
    assert result.error_message is None
    assert result.evidence_path is not None
    assert result.evidence_path.exists()

    # 2. Verify evidence JSON on disk
    with open(result.evidence_path, encoding="utf-8") as f:
        evidence = json.load(f)
        assert evidence["run_uuid"] == result.run_uuid
        assert evidence["target_volume"] == "mysql_data"
        assert evidence["status"] == "SUCCESS"
        assert evidence["passed"] is True
        assert "recorded_at" in evidence
        assert "volume_metadata" in evidence["details"]
        assert evidence["details"]["volume_metadata"]["driver"] == "local"

    # 3. Verify SQLite audit trail persistence
    trail = audit_logger.get_run_audit_trail(result.run_id)
    assert len(trail) >= 15

    # Check stage ordering in SQLite
    persisted_stages = [event.stage for event in trail]
    assert persisted_stages[0] == RecoveryStage.CHECK_DOCKER.value
    assert persisted_stages[-1] == RecoveryStage.FINALIZE_RUN.value

    # 4. Verify SQLite runs table record
    conn = audit_logger._get_connection()
    try:
        run_record = conn.execute(
            "SELECT run_uuid, status, final_stage, started_at, completed_at FROM runs WHERE id = ?;",
            (result.run_id,),
        ).fetchone()
        assert run_record["run_uuid"] == result.run_uuid
        assert run_record["status"] == "SUCCESS"
        assert run_record["final_stage"] == RecoveryStage.FINALIZE_RUN.value
        assert run_record["completed_at"] is not None
    finally:
        conn.close()

    # 5. Verify lock was cleaned up
    lock_file = Path("./logs/recovery.lock").resolve()
    assert not lock_file.exists()
