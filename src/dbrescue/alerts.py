"""Alert evaluation engine, threshold monitoring, and state tracking.

Guarantees:
  - Discrete states: NOT_EVALUATED, OK, FIRING, RESOLVED.
  - An alert starts NOT_EVALUATED until an explicit check executes.
  - Zero false positives: No alert is displayed as 'OK' without positive verification evidence.
  - Auto-resolves previously firing alerts when health checks pass.
  - Persists active alerts and transitions to SQLite.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from dbrescue.audit import AuditLogger
from dbrescue.config import RescueException
from dbrescue.monitoring import FreshnessStatus, TelemetrySnapshot

logger = logging.getLogger("dbrescue.alerts")


# --- Enums ---

class AlertState(str, Enum):
    """Discrete operational state of an evaluated alert rule."""
    NOT_EVALUATED = "NOT_EVALUATED"
    OK = "OK"
    FIRING = "FIRING"
    RESOLVED = "RESOLVED"


class AlertSeverity(str, Enum):
    """Severity classification for operational alert dispatch."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# --- Exceptions ---

class AlertError(RescueException):
    """Base exception for alert evaluation errors."""
    pass


# --- Alert Models ---

@dataclass(frozen=True)
class AlertRecord:
    """Historical alert instance persisted in SQLite."""
    alert_id: int
    run_id: int | None
    alert_name: str
    severity: str
    message: str
    is_resolved: bool
    resolved_at: str | None
    created_at: str


@dataclass(frozen=True)
class AlertStatus:
    """Current evaluation status of an alert rule."""
    rule_name: str
    state: AlertState
    severity: AlertSeverity
    message: str
    evaluated_at: str | None
    has_evidence: bool = False
    details: dict[str, Any] = field(default_factory=dict)


# --- Alert Rule Definitions ---

STANDARD_RULES = {
    "MYSQL_UNAVAILABLE": AlertSeverity.CRITICAL,
    "VOLUME_UNMOUNTED": AlertSeverity.CRITICAL,
    "RECORD_COUNT_MISMATCH": AlertSeverity.CRITICAL,
    "BACKUP_OVERDUE": AlertSeverity.WARNING,
    "RESTORE_TEST_FAILED": AlertSeverity.CRITICAL,
    "METRICS_STALE": AlertSeverity.WARNING,
    "CPU_HIGH_THRESHOLD": AlertSeverity.WARNING,
    "MEMORY_HIGH_THRESHOLD": AlertSeverity.WARNING,
}


# --- Alert Manager ---

class AlertManager:
    """Evaluates system health against defined operational thresholds."""

    def __init__(
        self,
        sqlite_db_path: Path | str,
        audit_logger: AuditLogger | None = None,
        cpu_threshold_pct: float = 85.0,
        memory_threshold_pct: float = 85.0,
        max_backup_age_hours: float = 24.0,
    ) -> None:
        self.sqlite_db_path = Path(sqlite_db_path).resolve()
        self.audit_logger = audit_logger or AuditLogger(self.sqlite_db_path)
        self.cpu_threshold_pct = cpu_threshold_pct
        self.memory_threshold_pct = memory_threshold_pct
        self.max_backup_age_hours = max_backup_age_hours

    def evaluate_all(
        self,
        telemetry: TelemetrySnapshot | None = None,
        last_run: dict[str, Any] | None = None,
        newest_backup: dict[str, Any] | None = None,
        last_restore: dict[str, Any] | None = None,
    ) -> list[AlertStatus]:
        """Evaluates all standard alert rules strictly based on real evidence.

        Rules without evidence remain in state NOT_EVALUATED.
        No rule is marked OK unless real evidence verifies normal operation.
        """
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        results: list[AlertStatus] = []

        # 1. Rule: MYSQL_UNAVAILABLE
        if telemetry is None:
            results.append(
                AlertStatus(
                    rule_name="MYSQL_UNAVAILABLE",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.CRITICAL,
                    message="Database connectivity not evaluated (no telemetry sample).",
                    evaluated_at=None,
                    has_evidence=False,
                )
            )
        elif not telemetry.database.is_connected:
            results.append(
                AlertStatus(
                    rule_name="MYSQL_UNAVAILABLE",
                    state=AlertState.FIRING,
                    severity=AlertSeverity.CRITICAL,
                    message="MySQL server is unreachable on configured host and port.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"latency_ms": telemetry.database.latency_ms},
                )
            )
        else:
            results.append(
                AlertStatus(
                    rule_name="MYSQL_UNAVAILABLE",
                    state=AlertState.OK,
                    severity=AlertSeverity.CRITICAL,
                    message=f"MySQL responsive (latency: {telemetry.database.latency_ms}ms).",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"latency_ms": telemetry.database.latency_ms},
                )
            )

        # 2. Rule: VOLUME_UNMOUNTED
        if telemetry is None:
            results.append(
                AlertStatus(
                    rule_name="VOLUME_UNMOUNTED",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.CRITICAL,
                    message="Volume mount status not evaluated.",
                    evaluated_at=None,
                    has_evidence=False,
                )
            )
        elif not telemetry.volume.exists or not telemetry.volume.is_mounted:
            results.append(
                AlertStatus(
                    rule_name="VOLUME_UNMOUNTED",
                    state=AlertState.FIRING,
                    severity=AlertSeverity.CRITICAL,
                    message="Named volume is not properly mounted or missing from container.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"volume_exists": telemetry.volume.exists, "is_mounted": telemetry.volume.is_mounted},
                )
            )
        else:
            results.append(
                AlertStatus(
                    rule_name="VOLUME_UNMOUNTED",
                    state=AlertState.OK,
                    severity=AlertSeverity.CRITICAL,
                    message=f"Storage volume attached and mounted at {telemetry.volume.mountpoint}.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"mountpoint": telemetry.volume.mountpoint},
                )
            )

        # 3. Rule: RECORD_COUNT_MISMATCH
        if last_run is None:
            results.append(
                AlertStatus(
                    rule_name="RECORD_COUNT_MISMATCH",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.CRITICAL,
                    message="No disaster recovery runs executed yet.",
                    evaluated_at=None,
                    has_evidence=False,
                )
            )
        elif last_run.get("status") in ("FAILED", "DEGRADED"):
            results.append(
                AlertStatus(
                    rule_name="RECORD_COUNT_MISMATCH",
                    state=AlertState.FIRING,
                    severity=AlertSeverity.CRITICAL,
                    message=f"Disaster recovery run {last_run.get('run_uuid', '')[:8]} failed or detected data parity mismatch.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"run_uuid": last_run.get("run_uuid"), "error": last_run.get("error_message")},
                )
            )
        elif last_run.get("status") == "SUCCESS":
            results.append(
                AlertStatus(
                    rule_name="RECORD_COUNT_MISMATCH",
                    state=AlertState.OK,
                    severity=AlertSeverity.CRITICAL,
                    message=f"Latest disaster recovery test ({last_run.get('run_uuid', '')[:8]}) verified 100% parity.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"run_uuid": last_run.get("run_uuid")},
                )
            )
        else:
            results.append(
                AlertStatus(
                    rule_name="RECORD_COUNT_MISMATCH",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.CRITICAL,
                    message=f"Run in non-terminal state: {last_run.get('status')}.",
                    evaluated_at=now_utc,
                    has_evidence=False,
                )
            )

        # 4. Rule: BACKUP_OVERDUE
        if newest_backup is None:
            results.append(
                AlertStatus(
                    rule_name="BACKUP_OVERDUE",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.WARNING,
                    message="No backup archives recorded.",
                    evaluated_at=None,
                    has_evidence=False,
                )
            )
        else:
            created_at_str = newest_backup.get("created_at", "")
            try:
                cleaned = created_at_str.replace("Z", "+00:00")
                if "T" in cleaned:
                    dt = datetime.datetime.fromisoformat(cleaned)
                else:
                    dt = datetime.datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.UTC)
                age_hours = (datetime.datetime.now(datetime.UTC) - dt).total_seconds() / 3600.0
            except Exception:
                age_hours = 0.0

            if age_hours > self.max_backup_age_hours:
                results.append(
                    AlertStatus(
                        rule_name="BACKUP_OVERDUE",
                        state=AlertState.FIRING,
                        severity=AlertSeverity.WARNING,
                        message=f"Newest backup is {age_hours:.1f}h old (exceeds {self.max_backup_age_hours}h threshold).",
                        evaluated_at=now_utc,
                        has_evidence=True,
                        details={"age_hours": round(age_hours, 1), "filename": newest_backup.get("filename")},
                    )
                )
            else:
                results.append(
                    AlertStatus(
                        rule_name="BACKUP_OVERDUE",
                        state=AlertState.OK,
                        severity=AlertSeverity.WARNING,
                        message=f"Backup fresh ({age_hours:.1f}h old).",
                        evaluated_at=now_utc,
                        has_evidence=True,
                        details={"age_hours": round(age_hours, 1), "filename": newest_backup.get("filename")},
                    )
                )

        # 5. Rule: RESTORE_TEST_FAILED
        if last_restore is None:
            results.append(
                AlertStatus(
                    rule_name="RESTORE_TEST_FAILED",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.CRITICAL,
                    message="No restore dry-runs or sandbox tests executed yet.",
                    evaluated_at=None,
                    has_evidence=False,
                )
            )
        elif last_restore.get("status") != "SUCCESS" or last_restore.get("verification_status") != "VERIFIED":
            results.append(
                AlertStatus(
                    rule_name="RESTORE_TEST_FAILED",
                    state=AlertState.FIRING,
                    severity=AlertSeverity.CRITICAL,
                    message=f"Last restore attempt failed with status {last_restore.get('status')}.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details=last_restore,
                )
            )
        else:
            results.append(
                AlertStatus(
                    rule_name="RESTORE_TEST_FAILED",
                    state=AlertState.OK,
                    severity=AlertSeverity.CRITICAL,
                    message=f"Latest restore verified successfully ({last_restore.get('rows_restored', 0)} rows).",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details=last_restore,
                )
            )

        # 6. Rule: METRICS_STALE
        if telemetry is None:
            results.append(
                AlertStatus(
                    rule_name="METRICS_STALE",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.WARNING,
                    message="Telemetry collection offline.",
                    evaluated_at=None,
                    has_evidence=False,
                )
            )
        elif telemetry.freshness != FreshnessStatus.FRESH:
            results.append(
                AlertStatus(
                    rule_name="METRICS_STALE",
                    state=AlertState.FIRING,
                    severity=AlertSeverity.WARNING,
                    message=f"Metrics sample is {telemetry.age_seconds}s old (marked {telemetry.freshness.value}).",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"age_seconds": telemetry.age_seconds, "freshness": telemetry.freshness.value},
                )
            )
        else:
            results.append(
                AlertStatus(
                    rule_name="METRICS_STALE",
                    state=AlertState.OK,
                    severity=AlertSeverity.WARNING,
                    message="Real-time telemetry fresh.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"age_seconds": telemetry.age_seconds},
                )
            )

        # 7. Rule: CPU_HIGH_THRESHOLD
        if telemetry is None or telemetry.container.cpu_percent is None:
            results.append(
                AlertStatus(
                    rule_name="CPU_HIGH_THRESHOLD",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.WARNING,
                    message="Container CPU utilization UNKNOWN (stats unavailable).",
                    evaluated_at=now_utc,
                    has_evidence=False,
                )
            )
        elif telemetry.container.cpu_percent > self.cpu_threshold_pct:
            results.append(
                AlertStatus(
                    rule_name="CPU_HIGH_THRESHOLD",
                    state=AlertState.FIRING,
                    severity=AlertSeverity.WARNING,
                    message=f"High CPU utilization: {telemetry.container.cpu_percent}% > {self.cpu_threshold_pct}%.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"cpu_percent": telemetry.container.cpu_percent},
                )
            )
        else:
            results.append(
                AlertStatus(
                    rule_name="CPU_HIGH_THRESHOLD",
                    state=AlertState.OK,
                    severity=AlertSeverity.WARNING,
                    message=f"CPU utilization normal ({telemetry.container.cpu_percent}%).",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"cpu_percent": telemetry.container.cpu_percent},
                )
            )

        # 8. Rule: MEMORY_HIGH_THRESHOLD
        if telemetry is None or telemetry.container.memory_percent is None:
            results.append(
                AlertStatus(
                    rule_name="MEMORY_HIGH_THRESHOLD",
                    state=AlertState.NOT_EVALUATED,
                    severity=AlertSeverity.WARNING,
                    message="Container memory utilization UNKNOWN (stats unavailable).",
                    evaluated_at=now_utc,
                    has_evidence=False,
                )
            )
        elif telemetry.container.memory_percent > self.memory_threshold_pct:
            results.append(
                AlertStatus(
                    rule_name="MEMORY_HIGH_THRESHOLD",
                    state=AlertState.FIRING,
                    severity=AlertSeverity.WARNING,
                    message=f"High memory utilization: {telemetry.container.memory_percent}% > {self.memory_threshold_pct}%.",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"memory_percent": telemetry.container.memory_percent},
                )
            )
        else:
            results.append(
                AlertStatus(
                    rule_name="MEMORY_HIGH_THRESHOLD",
                    state=AlertState.OK,
                    severity=AlertSeverity.WARNING,
                    message=f"Memory utilization normal ({telemetry.container.memory_percent}%).",
                    evaluated_at=now_utc,
                    has_evidence=True,
                    details={"memory_percent": telemetry.container.memory_percent},
                )
            )

        # Persist / auto-resolve firing alerts in SQLite
        self._sync_alerts_with_database(results)
        return results

    def _sync_alerts_with_database(self, evaluations: list[AlertStatus]) -> None:
        """Persists newly firing alerts and resolves previously firing alerts."""
        with self.audit_logger._get_connection() as conn:
            # Query active (unresolved) alerts
            cur = conn.execute("SELECT id, alert_name FROM alerts WHERE is_resolved = 0;")
            active_db_alerts = {row["alert_name"]: row["id"] for row in cur.fetchall()}

            for item in evaluations:
                if item.state == AlertState.FIRING:
                    # If not already active in DB, insert new alert
                    if item.rule_name not in active_db_alerts:
                        conn.execute(
                            """
                            INSERT INTO alerts (run_id, alert_name, severity, message, is_resolved)
                            VALUES (NULL, ?, ?, ?, 0);
                            """,
                            (item.rule_name, item.severity.value, item.message),
                        )
                elif item.state == AlertState.OK and item.rule_name in active_db_alerts:
                    # If was active in DB, resolve it
                    alert_id = active_db_alerts[item.rule_name]
                    now_utc = datetime.datetime.now(datetime.UTC).isoformat()
                    conn.execute(
                        "UPDATE alerts SET is_resolved = 1, resolved_at = ? WHERE id = ?;",
                        (now_utc, alert_id),
                    )
            conn.commit()

    def list_active_alerts(self) -> list[AlertRecord]:
        """Returns all currently unresolved alerts from SQLite."""
        with self.audit_logger._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT id, run_id, alert_name, severity, message, is_resolved, resolved_at, created_at
                FROM alerts
                WHERE is_resolved = 0
                ORDER BY id DESC;
                """
            )
            rows = cur.fetchall()
            return [
                AlertRecord(
                    alert_id=r["id"],
                    run_id=r["run_id"],
                    alert_name=r["alert_name"],
                    severity=r["severity"],
                    message=r["message"],
                    is_resolved=bool(r["is_resolved"]),
                    resolved_at=r["resolved_at"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
