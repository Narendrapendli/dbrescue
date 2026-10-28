"""Backup lifecycle retention engine with protective safety guards.

Enforces configurable retention policies (keep last N backups, max age days) with dry-run
support, mandatory confirmation tokens, and audit logging.
CRITICAL SAFETY GUARD: The newest verified backup is NEVER deleted under any circumstance.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
from dataclasses import dataclass
from pathlib import Path

from dbrescue.audit import AuditLogger
from dbrescue.config import RescueException

logger = logging.getLogger("dbrescue.retention")


# --- Exceptions ---

class RetentionError(RescueException):
    """Base exception for backup retention failures."""
    pass


class RetentionConfirmationRequiredError(RetentionError):
    """Raised when attempting destructive backup deletion without confirmation token."""
    pass


# --- Dataclasses ---

@dataclass(frozen=True)
class RetentionPolicy:
    """Configurable retention thresholds and safety rules."""
    keep_last_n: int = 5
    max_age_days: int = 30
    require_confirmation: bool = True


@dataclass(frozen=True)
class RetentionResult:
    """Outcome summary of a retention evaluation or purge."""
    evaluated_count: int
    deleted_count: int
    preserved_count: int
    deleted_backup_ids: list[int]
    deleted_files: list[str]
    is_dry_run: bool


# --- Retention Manager ---

class RetentionManager:
    """Evaluates and applies backup lifecycle retention policies with audit trails."""

    def __init__(
        self,
        sqlite_db_path: Path | str,
        backup_dir: Path | str = "./backups",
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.sqlite_db_path = Path(sqlite_db_path).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        self.audit_logger = audit_logger or AuditLogger(self.sqlite_db_path)

    def apply_retention_policy(
        self,
        policy: RetentionPolicy | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> RetentionResult:
        """Applies retention rules to historical backup archives.

        Guarantees:
          - The newest verified backup ('VERIFIED_FILE' or 'RESTORE_VERIFIED') is NEVER purged.
          - Non-dry-run purge strictly requires 'confirm=True'.
          - All purged records are marked EXPIRED and logged in SQLite audit trail.
        """
        active_policy = policy or RetentionPolicy()

        if not dry_run and active_policy.require_confirmation and not confirm:
            raise RetentionConfirmationRequiredError(
                "Safety Guard: Destructive backup retention purge requires explicit 'confirm=True' token. "
                "Operation aborted."
            )

        # Retrieve all active backups ordered newest to oldest
        backups = self.audit_logger.list_backups(limit=500)
        # Exclude already EXPIRED records from evaluation
        active_backups = [b for b in backups if b.get("status") != "EXPIRED"]

        if not active_backups:
            return RetentionResult(
                evaluated_count=0,
                deleted_count=0,
                preserved_count=0,
                deleted_backup_ids=[],
                deleted_files=[],
                is_dry_run=dry_run,
            )

        now = datetime.datetime.now(datetime.UTC)

        # 1. Identify the NEWEST verified backup (CRITICAL SAFETY GUARD)
        newest_verified_id: int | None = None
        for b in active_backups:
            if b.get("status") in ("VERIFIED_FILE", "RESTORE_VERIFIED"):
                newest_verified_id = b["id"]
                logger.info(
                    "Identified newest verified backup [ID=%d, '%s']. Strictly protected from retention purge.",
                    b["id"],
                    b["filename"],
                )
                break

        to_delete_ids: list[int] = []
        to_delete_files: list[str] = []
        preserved_ids: list[int] = []

        # 2. Evaluate backups
        for idx, b in enumerate(active_backups):
            b_id = b["id"]
            filename = b["filename"]

            # Always protect the newest verified backup
            if b_id == newest_verified_id:
                preserved_ids.append(b_id)
                continue

            # Keep last N regardless of age
            if idx < active_policy.keep_last_n:
                preserved_ids.append(b_id)
                continue

            # Check age against max_age_days
            created_at_str = b.get("created_at", "")
            try:
                # Handle ISO 8601 or SQLite format
                cleaned_date = created_at_str.replace("Z", "+00:00")
                if "T" in cleaned_date:
                    dt = datetime.datetime.fromisoformat(cleaned_date)
                else:
                    dt = datetime.datetime.strptime(cleaned_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.UTC)

                age_days = (now - dt).total_seconds() / 86400.0
            except Exception:
                age_days = 0.0

            if age_days > active_policy.max_age_days:
                to_delete_ids.append(b_id)
                to_delete_files.append(filename)
            else:
                preserved_ids.append(b_id)

        # 3. Execute purge if not dry-run
        if not dry_run and to_delete_ids:
            for b_id, fname in zip(to_delete_ids, to_delete_files, strict=True):
                target_file = self.backup_dir / fname
                if target_file.exists():
                    with contextlib.suppress(OSError):
                        target_file.unlink(missing_ok=True)
                        logger.info("Deleted expired backup artifact on disk: %s", fname)

                # Mark status as EXPIRED in SQLite
                self.audit_logger.update_backup_status(b_id, "EXPIRED")

                # Log audit event
                self.audit_logger.log_event(
                    run_id=None,
                    stage="RETENTION_PURGE",
                    event_type="RETENTION_PURGE",
                    severity="INFO",
                    message=f"Purged expired backup artifact [ID={b_id}, file={fname}]",
                    details={"backup_id": b_id, "filename": fname},
                )

        return RetentionResult(
            evaluated_count=len(active_backups),
            deleted_count=len(to_delete_ids),
            preserved_count=len(preserved_ids),
            deleted_backup_ids=to_delete_ids,
            deleted_files=to_delete_files,
            is_dry_run=dry_run,
        )
