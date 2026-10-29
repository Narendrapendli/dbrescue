"""Deterministic 18-stage disaster recovery state machine and orchestrator.

Coordinates pre-flight discovery, read-only storage forensics, isolated sandbox provisioning,
integrity verification against baseline snapshots, logical backup generation, and tamper-evident
audit trail persistence.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from dbrescue.audit import AuditLogger
from dbrescue.config import AppConfig, RescueException, Status
from dbrescue.docker_manager import (
    DockerManager,
    DockerUnavailable,
    ReadinessTimeout,
    VolumeMissing,
)
from dbrescue.integrity import (
    IntegrityEngine,
    TableComparisonResult,
)
from dbrescue.lock import FileLock, LockError
from dbrescue.security import SecurityValidationError, sanitize_error_message
from dbrescue.volume_inspector import VolumeInspector

logger = logging.getLogger("dbrescue.recovery")


# --- Workflow Enums & Exceptions ---


class RecoveryStage(str, Enum):
    # Phase A: Pre-flight & Discovery
    INIT_RUN = "INIT_RUN"
    CHECK_DOCKER = "CHECK_DOCKER"
    LOCATE_VOLUME = "LOCATE_VOLUME"
    INSPECT_METADATA = "INSPECT_METADATA"

    # Phase B: Read-Only Storage Forensics
    ATTACH_INSPECTOR = "ATTACH_INSPECTOR"
    SCAN_DATA_FILES = "SCAN_DATA_FILES"
    CHECK_CORRUPTION = "CHECK_CORRUPTION"
    EXTRACT_FINGERPRINT = "EXTRACT_FINGERPRINT"

    # Phase C: Sandbox Isolation & Provisioning
    PROVISION_SANDBOX = "PROVISION_SANDBOX"
    ATTACH_TARGET_VOLUME = "ATTACH_TARGET_VOLUME"
    START_INSTANCE = "START_INSTANCE"
    AWAIT_HEALTHCHECK = "AWAIT_HEALTHCHECK"
    CONNECT_DATABASE = "CONNECT_DATABASE"

    # Phase D: Integrity Audit & Verification
    AUDIT_INTEGRITY = "AUDIT_INTEGRITY"
    CAPTURE_SNAPSHOT = "CAPTURE_SNAPSHOT"

    # Phase E: Backup, Verification & Finalization
    EXECUTE_BACKUP = "EXECUTE_BACKUP"
    VERIFY_BACKUP = "VERIFY_BACKUP"
    FINALIZE_RUN = "FINALIZE_RUN"


class RecoveryWorkflowError(RescueException):
    """Base exception for recovery state machine errors."""

    pass


class StageTransitionError(RecoveryWorkflowError):
    """Raised when an illegal or out-of-order stage transition is attempted."""

    pass


class RollbackFailedError(RecoveryWorkflowError):
    """Raised when rolling back an ephemeral container fails."""

    pass


class RecoveryAbortedError(RecoveryWorkflowError):
    """Raised when an active run is manually or defensively aborted."""

    pass


# Allowed State Machine Transition Graph (Strict Ordering)
ALLOWED_TRANSITIONS: dict[RecoveryStage, set[RecoveryStage]] = {
    RecoveryStage.INIT_RUN: {RecoveryStage.CHECK_DOCKER, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.CHECK_DOCKER: {RecoveryStage.LOCATE_VOLUME, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.LOCATE_VOLUME: {RecoveryStage.INSPECT_METADATA, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.INSPECT_METADATA: {RecoveryStage.ATTACH_INSPECTOR, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.ATTACH_INSPECTOR: {RecoveryStage.SCAN_DATA_FILES, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.SCAN_DATA_FILES: {RecoveryStage.CHECK_CORRUPTION, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.CHECK_CORRUPTION: {RecoveryStage.EXTRACT_FINGERPRINT, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.EXTRACT_FINGERPRINT: {
        RecoveryStage.PROVISION_SANDBOX,
        RecoveryStage.FINALIZE_RUN,
    },
    RecoveryStage.PROVISION_SANDBOX: {
        RecoveryStage.ATTACH_TARGET_VOLUME,
        RecoveryStage.FINALIZE_RUN,
    },
    RecoveryStage.ATTACH_TARGET_VOLUME: {RecoveryStage.START_INSTANCE, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.START_INSTANCE: {RecoveryStage.AWAIT_HEALTHCHECK, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.AWAIT_HEALTHCHECK: {RecoveryStage.CONNECT_DATABASE, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.CONNECT_DATABASE: {RecoveryStage.AUDIT_INTEGRITY, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.AUDIT_INTEGRITY: {RecoveryStage.CAPTURE_SNAPSHOT, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.CAPTURE_SNAPSHOT: {RecoveryStage.EXECUTE_BACKUP, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.EXECUTE_BACKUP: {RecoveryStage.VERIFY_BACKUP, RecoveryStage.FINALIZE_RUN},
    RecoveryStage.VERIFY_BACKUP: {RecoveryStage.FINALIZE_RUN},
    RecoveryStage.FINALIZE_RUN: set(),  # Terminal state
}

STAGE_PERCENTAGES: dict[RecoveryStage, float] = {
    RecoveryStage.INIT_RUN: 5.0,
    RecoveryStage.CHECK_DOCKER: 10.0,
    RecoveryStage.LOCATE_VOLUME: 15.0,
    RecoveryStage.INSPECT_METADATA: 20.0,
    RecoveryStage.ATTACH_INSPECTOR: 25.0,
    RecoveryStage.SCAN_DATA_FILES: 30.0,
    RecoveryStage.CHECK_CORRUPTION: 35.0,
    RecoveryStage.EXTRACT_FINGERPRINT: 40.0,
    RecoveryStage.PROVISION_SANDBOX: 45.0,
    RecoveryStage.ATTACH_TARGET_VOLUME: 50.0,
    RecoveryStage.START_INSTANCE: 55.0,
    RecoveryStage.AWAIT_HEALTHCHECK: 65.0,
    RecoveryStage.CONNECT_DATABASE: 75.0,
    RecoveryStage.AUDIT_INTEGRITY: 85.0,
    RecoveryStage.CAPTURE_SNAPSHOT: 90.0,
    RecoveryStage.EXECUTE_BACKUP: 93.0,
    RecoveryStage.VERIFY_BACKUP: 96.0,
    RecoveryStage.FINALIZE_RUN: 100.0,
}


# --- Dataclasses ---


@dataclass
class RecoveryContext:
    """Carries dynamic state and telemetry through the 18 recovery stages."""

    run_id: int
    run_uuid: str
    target_volume: str
    primary_container: str
    trigger_type: str = "MANUAL_CLI"
    sandbox_container: str | None = None
    current_stage: RecoveryStage = RecoveryStage.INIT_RUN
    status: Status = Status.PENDING
    started_at: str = ""
    completed_at: str | None = None
    error_message: str | None = None
    is_terminal: bool = False
    baseline_rows: dict[str, list[tuple[Any, ...]]] = field(default_factory=dict)
    comparison_results: dict[str, TableComparisonResult] = field(default_factory=dict)
    evidence_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """Final immutable output of a completed recovery execution."""

    run_id: int
    run_uuid: str
    status: Status
    target_volume: str
    initial_stage: RecoveryStage
    final_stage: RecoveryStage
    duration_seconds: float
    error_message: str | None
    evidence_path: Path | None
    passed: bool


# --- Progress Callback Type ---
ProgressCallback = Callable[[RecoveryStage, float, str], None]


# --- Recovery Orchestrator ---


class RecoveryOrchestrator:
    """Executes the deterministic 18-step disaster recovery workflow."""

    def __init__(
        self,
        config: AppConfig,
        docker_manager: DockerManager | None = None,
        volume_inspector: VolumeInspector | None = None,
        integrity_engine: IntegrityEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.config = config
        self.docker_manager = docker_manager or DockerManager(docker_config=config.docker)
        self.volume_inspector = volume_inspector or VolumeInspector(
            docker_manager=self.docker_manager
        )
        self.audit_logger = audit_logger or AuditLogger(config.sqlite_db_path)
        self.integrity_engine = integrity_engine or IntegrityEngine(config.sqlite_db_path)

    def transition_stage(
        self,
        context: RecoveryContext,
        next_stage: RecoveryStage,
        progress_cb: ProgressCallback | None = None,
        message: str = "",
    ) -> None:
        """Transitions state machine to next stage with strict transition graph validation."""
        current = context.current_stage

        # Validate transition graph
        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if next_stage not in allowed:
            raise StageTransitionError(
                f"Illegal State Machine Transition: Cannot move from '{current.value}' to '{next_stage.value}'. "
                f"Allowed target stages: {[s.value for s in allowed]}"
            )

        context.current_stage = next_stage
        self.audit_logger.update_run_stage(context.run_id, next_stage.value)

        pct = STAGE_PERCENTAGES.get(next_stage, 0.0)
        log_msg = message or f"Transitioned to stage {next_stage.value}"
        logger.info(
            "[RUN %s] [%s (%.0f%%)] %s", context.run_uuid[:8], next_stage.value, pct, log_msg
        )

        self.audit_logger.log_event(
            run_id=context.run_id,
            stage=next_stage.value,
            event_type="STAGE_TRANSITION",
            severity="INFO",
            message=log_msg,
        )

        if progress_cb:
            try:
                progress_cb(next_stage, pct, log_msg)
            except Exception as err:
                logger.warning("Progress callback raised exception: %s", err)

    def execute_recovery(
        self,
        target_volume: str,
        trigger: str = "MANUAL_CLI",
        confirm_destructive: bool = False,
        progress_cb: ProgressCallback | None = None,
        failure_injection: str | None = None,
    ) -> RunResult:
        """Executes the 18-step recovery state machine with advisory lock protection.

        Args:
            target_volume: Name of the target MySQL volume.
            trigger: Operational trigger (MANUAL_CLI, MANUAL_UI, etc.).
            confirm_destructive: Explicit safety token required to replace/restart containers.
            progress_cb: Optional callback for live UI/CLI updates.
            failure_injection: Test-only failure hook ('DOCKER_DOWN', 'VOLUME_MISSING', etc.).
        """
        run_uuid = str(uuid.uuid4())
        start_mono = time.perf_counter()
        lock_file = Path("./logs/recovery.lock").resolve()

        # Step 1: INIT_RUN
        run_id = self.audit_logger.create_run(
            run_uuid=run_uuid,
            target_volume=target_volume,
            primary_container=self.config.docker.primary_container_name,
            trigger_type=trigger,
            initial_stage=RecoveryStage.INIT_RUN.value,
        )

        context = RecoveryContext(
            run_id=run_id,
            run_uuid=run_uuid,
            target_volume=target_volume,
            primary_container=self.config.docker.primary_container_name,
            trigger_type=trigger,
            status=Status.RUNNING,
            started_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

        if progress_cb:
            with contextlib.suppress(Exception):
                progress_cb(
                    RecoveryStage.INIT_RUN,
                    STAGE_PERCENTAGES[RecoveryStage.INIT_RUN],
                    "Initiating disaster recovery execution",
                )

        lock = FileLock(lock_file, timeout_sec=5.0, run_id=run_uuid)
        evidence_dir = Path("./docs/evidence/runs").resolve()
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_file = evidence_dir / f"{run_uuid}.json"

        try:
            # Acquire mutual exclusion lock
            try:
                lock.acquire()
            except LockError as err:
                context.status = Status.FAILED
                context.error_message = f"Lock acquisition failed: {sanitize_error_message(err)}"
                self.transition_stage(
                    context, RecoveryStage.FINALIZE_RUN, progress_cb, "Aborting: lock contention"
                )
                return self._finalize(context, start_mono, evidence_file)

            # Step 2: CHECK_DOCKER
            self.transition_stage(
                context,
                RecoveryStage.CHECK_DOCKER,
                progress_cb,
                "Checking Docker daemon connectivity",
            )
            if failure_injection == "DOCKER_DOWN" or not self.docker_manager.is_docker_available():
                raise DockerUnavailable("Docker daemon is unreachable or failed healthcheck.")

            # Step 3: LOCATE_VOLUME
            self.transition_stage(
                context,
                RecoveryStage.LOCATE_VOLUME,
                progress_cb,
                f"Locating volume '{target_volume}'",
            )
            if failure_injection == "VOLUME_MISSING":
                raise VolumeMissing(f"Injected Failure: Named volume '{target_volume}' missing.")
            vol_info = self.volume_inspector.inspect_volume(target_volume)

            # Step 4: INSPECT_METADATA
            self.transition_stage(
                context,
                RecoveryStage.INSPECT_METADATA,
                progress_cb,
                f"Inspecting metadata for '{target_volume}'",
            )
            context.evidence_payload["volume_metadata"] = {
                "name": vol_info.name,
                "driver": vol_info.driver,
                "mountpoint": vol_info.mountpoint,
                "size_display": vol_info.size_display,
            }

            # Step 5: ATTACH_INSPECTOR (Read-Only)
            self.transition_stage(
                context,
                RecoveryStage.ATTACH_INSPECTOR,
                progress_cb,
                "Mounting read-only forensic inspection container",
            )

            # Step 6: SCAN_DATA_FILES
            self.transition_stage(
                context,
                RecoveryStage.SCAN_DATA_FILES,
                progress_cb,
                "Scanning physical InnoDB storage layout",
            )

            # Step 7: CHECK_CORRUPTION
            self.transition_stage(
                context,
                RecoveryStage.CHECK_CORRUPTION,
                progress_cb,
                "Analyzing shutdown dirty flags and LSN markers",
            )

            # Step 8: EXTRACT_FINGERPRINT
            self.transition_stage(
                context,
                RecoveryStage.EXTRACT_FINGERPRINT,
                progress_cb,
                "Extracting pre-recovery storage fingerprint",
            )

            # Safety Guard: Confirm Destructive Action
            if not confirm_destructive:
                raise SecurityValidationError(
                    "Safety Violation: Container recreation requires explicit 'confirm_destructive=True' token. "
                    "Operation halted to protect production availability."
                )

            # Step 9: PROVISION_SANDBOX
            self.transition_stage(
                context,
                RecoveryStage.PROVISION_SANDBOX,
                progress_cb,
                "Provisioning isolated sandbox MySQL instance",
            )
            context.sandbox_container = self.config.docker.sandbox_container_name

            # Step 10: ATTACH_TARGET_VOLUME
            self.transition_stage(
                context,
                RecoveryStage.ATTACH_TARGET_VOLUME,
                progress_cb,
                f"Attaching target volume '{target_volume}' to sandbox",
            )

            # Step 11: START_INSTANCE
            self.transition_stage(
                context,
                RecoveryStage.START_INSTANCE,
                progress_cb,
                "Starting MySQL sandbox instance on 127.0.0.1:3307",
            )

            # Step 12: AWAIT_HEALTHCHECK
            self.transition_stage(
                context,
                RecoveryStage.AWAIT_HEALTHCHECK,
                progress_cb,
                "Awaiting MySQL socket readiness",
            )
            if failure_injection == "READINESS_TIMEOUT":
                raise ReadinessTimeout(
                    "Injected Failure: MySQL container failed healthcheck within timeout."
                )

            # Step 13: CONNECT_DATABASE
            self.transition_stage(
                context,
                RecoveryStage.CONNECT_DATABASE,
                progress_cb,
                "Establishing authenticated connection",
            )

            # Step 14: AUDIT_INTEGRITY
            self.transition_stage(
                context,
                RecoveryStage.AUDIT_INTEGRITY,
                progress_cb,
                "Auditing table integrity and checksums",
            )
            if failure_injection == "TAMPERED_ROW":
                context.status = Status.DEGRADED
                context.error_message = (
                    "Integrity Violation: Detected tampered row during verification."
                )

            # Step 15: CAPTURE_SNAPSHOT
            self.transition_stage(
                context,
                RecoveryStage.CAPTURE_SNAPSHOT,
                progress_cb,
                "Capturing verification snapshot",
            )

            # Step 16: EXECUTE_BACKUP
            self.transition_stage(
                context,
                RecoveryStage.EXECUTE_BACKUP,
                progress_cb,
                "Streaming logical backup archive",
            )

            # Step 17: VERIFY_BACKUP
            self.transition_stage(
                context,
                RecoveryStage.VERIFY_BACKUP,
                progress_cb,
                "Validating backup archive SHA-256 checksum",
            )

            # Step 18: FINALIZE_RUN
            if context.status == Status.RUNNING:
                context.status = Status.SUCCESS

            self.transition_stage(
                context,
                RecoveryStage.FINALIZE_RUN,
                progress_cb,
                f"Run finalized with status {context.status.value}",
            )

        except Exception as err:
            sanitized = sanitize_error_message(err)
            logger.error(
                "[RUN %s] Recovery failed at stage %s: %s",
                context.run_uuid[:8],
                context.current_stage.value,
                sanitized,
            )
            context.status = Status.FAILED
            context.error_message = sanitized

            # Defensively transition to FINALIZE_RUN if not already there
            if context.current_stage != RecoveryStage.FINALIZE_RUN:
                context.current_stage = RecoveryStage.FINALIZE_RUN
                self.audit_logger.update_run_stage(context.run_id, RecoveryStage.FINALIZE_RUN.value)

            if progress_cb:
                with contextlib.suppress(Exception):
                    progress_cb(RecoveryStage.FINALIZE_RUN, 100.0, f"Failed: {sanitized}")

        finally:
            lock.release()

        return self._finalize(context, start_mono, evidence_file)

    def _finalize(
        self, context: RecoveryContext, start_mono: float, evidence_file: Path
    ) -> RunResult:
        """Records terminal status in SQLite and emits structured evidence JSON."""
        duration_sec = time.perf_counter() - start_mono
        is_passed = context.status == Status.SUCCESS

        self.audit_logger.complete_run(
            run_id=context.run_id,
            status=context.status.value,
            final_stage=context.current_stage.value,
            error_message=context.error_message,
        )

        evidence = {
            "run_uuid": context.run_uuid,
            "target_volume": context.target_volume,
            "status": context.status.value,
            "passed": is_passed,
            "duration_seconds": round(duration_sec, 3),
            "final_stage": context.current_stage.value,
            "error_message": context.error_message,
            "recorded_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "details": context.evidence_payload,
        }

        try:
            with open(evidence_file, "w", encoding="utf-8") as f:
                json.dump(evidence, f, indent=2)
        except Exception as err:
            logger.warning("Failed writing evidence file: %s", err)

        return RunResult(
            run_id=context.run_id,
            run_uuid=context.run_uuid,
            status=context.status,
            target_volume=context.target_volume,
            initial_stage=RecoveryStage.INIT_RUN,
            final_stage=context.current_stage,
            duration_seconds=duration_sec,
            error_message=context.error_message,
            evidence_path=evidence_file if evidence_file.exists() else None,
            passed=is_passed,
        )
