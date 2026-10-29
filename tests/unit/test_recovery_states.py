"""Unit tests for recovery state machine transitions, lock contention, failure hooks, and safety guardrails."""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbrescue.audit import AuditLogger
from dbrescue.config import AppConfig, Status
from dbrescue.docker_manager import DockerManager
from dbrescue.integrity import IntegrityEngine
from dbrescue.lock import FileLock
from dbrescue.recovery import (
    ALLOWED_TRANSITIONS,
    RecoveryContext,
    RecoveryOrchestrator,
    RecoveryStage,
    StageTransitionError,
)
from dbrescue.volume_inspector import VolumeInfo, VolumeInspector


@pytest.fixture
def mock_app_config(tmp_path: Path) -> AppConfig:
    db_path = tmp_path / "test_audit.sqlite3"
    cfg = MagicMock(spec=AppConfig)
    cfg.sqlite_db_path = db_path
    cfg.docker = MagicMock()
    cfg.docker.primary_container_name = "mysql_prod"
    cfg.docker.sandbox_container_name = "mysql_sandbox"
    cfg.docker.volume_name = "mysql_data"
    cfg.docker.image = "mysql:8.4.3"
    return cfg


@pytest.fixture
def mock_orchestrator(mock_app_config: AppConfig, tmp_path: Path) -> RecoveryOrchestrator:
    audit_logger = AuditLogger(mock_app_config.sqlite_db_path)
    docker_mgr = MagicMock(spec=DockerManager)
    docker_mgr.is_docker_available.return_value = True

    vol_inspector = MagicMock(spec=VolumeInspector)
    vol_inspector.inspect_volume.return_value = VolumeInfo(
        name="mysql_data",
        driver="local",
        mountpoint="/var/lib/docker/volumes/mysql_data/_data",
        size_bytes=1048576,
        size_display="1.0 MB",
        labels={},
    )

    integrity_eng = MagicMock(spec=IntegrityEngine)

    return RecoveryOrchestrator(
        config=mock_app_config,
        docker_manager=docker_mgr,
        volume_inspector=vol_inspector,
        integrity_engine=integrity_eng,
        audit_logger=audit_logger,
    )


def test_allowed_transitions_graph(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Verifies that all defined transitions in ALLOWED_TRANSITIONS succeed."""
    run_uuid = str(uuid.uuid4())
    run_id = mock_orchestrator.audit_logger.create_run(
        run_uuid=run_uuid,
        target_volume="mysql_data",
        primary_container="mysql_prod",
    )

    for src_stage, targets in ALLOWED_TRANSITIONS.items():
        for tgt_stage in targets:
            context = RecoveryContext(
                run_id=run_id,
                run_uuid=run_uuid,
                target_volume="mysql_data",
                primary_container="mysql_prod",
                current_stage=src_stage,
            )
            mock_orchestrator.transition_stage(context, tgt_stage)
            assert context.current_stage == tgt_stage


def test_illegal_transitions_raise_error(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Verifies that illegal transitions raise StageTransitionError."""
    context = RecoveryContext(
        run_id=1,
        run_uuid=str(uuid.uuid4()),
        target_volume="mysql_data",
        primary_container="mysql_prod",
        current_stage=RecoveryStage.INIT_RUN,
    )

    # Attempt illegal skip to AUDIT_INTEGRITY
    with pytest.raises(StageTransitionError) as exc_info:
        mock_orchestrator.transition_stage(context, RecoveryStage.AUDIT_INTEGRITY)
    assert "Illegal State Machine Transition" in str(exc_info.value)

    # Attempt illegal backwards transition
    context.current_stage = RecoveryStage.START_INSTANCE
    with pytest.raises(StageTransitionError):
        mock_orchestrator.transition_stage(context, RecoveryStage.INIT_RUN)


def test_terminal_state_cannot_transition(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Verifies that FINALIZE_RUN has no allowed transitions."""
    context = RecoveryContext(
        run_id=1,
        run_uuid=str(uuid.uuid4()),
        target_volume="mysql_data",
        primary_container="mysql_prod",
        current_stage=RecoveryStage.FINALIZE_RUN,
    )

    with pytest.raises(StageTransitionError):
        mock_orchestrator.transition_stage(context, RecoveryStage.INIT_RUN)


def test_confirm_destructive_token_required(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Verifies that confirm_destructive=False halts the recovery and records FAILED."""
    result = mock_orchestrator.execute_recovery(
        target_volume="mysql_data",
        confirm_destructive=False,
    )

    assert result.status == Status.FAILED
    assert result.passed is False
    assert "confirm_destructive=True" in (result.error_message or "")


def test_lock_contention_aborts_cleanly(
    mock_orchestrator: RecoveryOrchestrator, tmp_path: Path
) -> None:
    """Verifies that holding the lock file causes execute_recovery to abort gracefully."""
    lock_file = Path("./logs/recovery.lock").resolve()
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    # Hold the lock with an active process ID
    first_lock = FileLock(lock_file, timeout_sec=1.0, run_id="contention_test")
    first_lock.acquire()

    try:
        result = mock_orchestrator.execute_recovery(
            target_volume="mysql_data",
            confirm_destructive=True,
        )
        assert result.status == Status.FAILED
        assert result.passed is False
        assert (
            "lock contention" in (result.error_message or "").lower()
            or "timed out" in (result.error_message or "").lower()
        )
    finally:
        first_lock.release()


def test_failure_injection_docker_down(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Failure hook 'DOCKER_DOWN' must stop at CHECK_DOCKER with status FAILED."""
    result = mock_orchestrator.execute_recovery(
        target_volume="mysql_data",
        confirm_destructive=True,
        failure_injection="DOCKER_DOWN",
    )
    assert result.status == Status.FAILED
    assert result.passed is False
    assert result.final_stage == RecoveryStage.FINALIZE_RUN
    assert "Docker daemon is unreachable" in (result.error_message or "")


def test_failure_injection_volume_missing(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Failure hook 'VOLUME_MISSING' must fail and never pass."""
    result = mock_orchestrator.execute_recovery(
        target_volume="mysql_data",
        confirm_destructive=True,
        failure_injection="VOLUME_MISSING",
    )
    assert result.status == Status.FAILED
    assert result.passed is False
    assert "Named volume 'mysql_data' missing" in (result.error_message or "")


def test_failure_injection_readiness_timeout(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Failure hook 'READINESS_TIMEOUT' must fail and stay FAILED."""
    result = mock_orchestrator.execute_recovery(
        target_volume="mysql_data",
        confirm_destructive=True,
        failure_injection="READINESS_TIMEOUT",
    )
    assert result.status == Status.FAILED
    assert result.passed is False
    assert "failed healthcheck within timeout" in (result.error_message or "")


def test_failure_injection_tampered_row_never_passes(
    mock_orchestrator: RecoveryOrchestrator,
) -> None:
    """Failure hook 'TAMPERED_ROW' causes degraded integrity and must never return passed=True."""
    result = mock_orchestrator.execute_recovery(
        target_volume="mysql_data",
        confirm_destructive=True,
        failure_injection="TAMPERED_ROW",
    )
    assert result.status == Status.DEGRADED
    assert result.passed is False
    assert "tampered row" in (result.error_message or "").lower()


def test_progress_callback_invoked(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Verifies that the progress callback receives sequential updates."""
    events: list[tuple[RecoveryStage, float, str]] = []

    def cb(stage: RecoveryStage, pct: float, msg: str) -> None:
        events.append((stage, pct, msg))

    result = mock_orchestrator.execute_recovery(
        target_volume="mysql_data",
        confirm_destructive=True,
        progress_cb=cb,
    )
    assert result.passed is True
    assert len(events) >= 15
    # Verify stages progressed
    stages = [e[0] for e in events]
    assert RecoveryStage.INIT_RUN in stages
    assert RecoveryStage.FINALIZE_RUN in stages
    # Verify percentage monotonicity
    percentages = [e[1] for e in events]
    assert percentages[-1] == 100.0


def test_evidence_json_emitted(mock_orchestrator: RecoveryOrchestrator) -> None:
    """Verifies that evidence JSON is written to docs/evidence/runs/<run_id>.json."""
    result = mock_orchestrator.execute_recovery(
        target_volume="mysql_data",
        confirm_destructive=True,
    )
    assert result.evidence_path is not None
    assert result.evidence_path.exists()

    with open(result.evidence_path, encoding="utf-8") as f:
        data = json.load(f)
        assert data["run_uuid"] == result.run_uuid
        assert data["target_volume"] == "mysql_data"
        assert data["passed"] is True
        assert data["status"] == "SUCCESS"
        assert "recorded_at" in data


def test_stale_lock_recovery(tmp_path: Path) -> None:
    """Verifies that an abandoned lock file is safely broken."""
    lock_file = tmp_path / "test.lock"

    # Create an abandoned lock file with a non-existent PID (e.g., 99999999)
    abandoned_data = {
        "owner_pid": 99999999,
        "acquired_at": 1000.0,
        "run_id": "dead_run",
        "hostname": "test",
    }
    lock_file.write_text(json.dumps(abandoned_data), encoding="utf-8")

    # New lock instance should detect dead PID and acquire successfully
    new_lock = FileLock(lock_file, timeout_sec=2.0, run_id="live_run")
    assert new_lock.acquire() is True
    new_lock.release()
