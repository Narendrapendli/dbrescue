"""Prometheus metrics exporter exposing real dbrescue operational indicators.

Exposes standard Prometheus gauges bound to 127.0.0.1:9108:
  - dbrescue_last_test_status (1=SUCCESS, 0=FAILED/DEGRADED, -1=NOT_RUN)
  - dbrescue_last_test_duration_seconds
  - dbrescue_last_backup_age_seconds
  - dbrescue_last_restore_verified (1=VERIFIED, 0=OTHERWISE)
  - dbrescue_record_count_baseline
  - dbrescue_record_count_recovered
  - dbrescue_database_latency_ms
  - dbrescue_container_cpu_percent
  - dbrescue_container_memory_bytes
  - dbrescue_active_alerts_count
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path

from prometheus_client import Gauge, start_http_server

from dbrescue.audit import AuditLogger
from dbrescue.config import RescueException
from dbrescue.monitoring import TelemetrySnapshot

logger = logging.getLogger("dbrescue.metrics_exporter")


class MetricsExporterError(RescueException):
    """Base exception for Prometheus metrics exporter errors."""

    pass


# --- Prometheus Gauges ---

GAUGE_LAST_TEST_STATUS = Gauge(
    "dbrescue_last_test_status",
    "Discrete status of latest disaster recovery run (1=SUCCESS, 0=FAILED/DEGRADED, -1=NOT_RUN)",
)
GAUGE_LAST_TEST_DURATION = Gauge(
    "dbrescue_last_test_duration_seconds",
    "Execution duration of latest disaster recovery run in seconds",
)
GAUGE_LAST_BACKUP_AGE = Gauge(
    "dbrescue_last_backup_age_seconds",
    "Age of newest valid backup archive in seconds",
)
GAUGE_LAST_RESTORE_VERIFIED = Gauge(
    "dbrescue_last_restore_verified",
    "Whether the latest restore verification check passed (1=VERIFIED, 0=OTHERWISE)",
)
GAUGE_RECORD_COUNT_BASELINE = Gauge(
    "dbrescue_record_count_baseline",
    "Baseline row count captured during latest disaster recovery run",
)
GAUGE_RECORD_COUNT_RECOVERED = Gauge(
    "dbrescue_record_count_recovered",
    "Recovered row count verified after container reconstitution",
)
GAUGE_DB_LATENCY = Gauge(
    "dbrescue_database_latency_ms",
    "Real-time database query round-trip latency in milliseconds",
)
GAUGE_CONTAINER_CPU = Gauge(
    "dbrescue_container_cpu_percent",
    "Real container CPU percentage from Docker stats",
)
GAUGE_CONTAINER_MEMORY = Gauge(
    "dbrescue_container_memory_bytes",
    "Real container memory usage in bytes",
)
GAUGE_ACTIVE_ALERTS = Gauge(
    "dbrescue_active_alerts_count",
    "Current count of active unresolved alerts",
)


def update_metrics_from_state(
    sqlite_db_path: Path | str,
    telemetry: TelemetrySnapshot | None = None,
) -> dict[str, float]:
    """Queries SQLite operational metadata and updates Prometheus gauges with real measurements."""
    audit_logger = AuditLogger(sqlite_db_path)
    snapshot_values: dict[str, float] = {}

    with audit_logger._get_connection() as conn:
        # 1. Latest Run Status & Duration
        cur = conn.execute(
            """
            SELECT status, started_at, completed_at
            FROM runs
            ORDER BY id DESC LIMIT 1;
            """
        )
        run_row = cur.fetchone()
        if run_row:
            st = run_row["status"]
            val = 1.0 if st == "SUCCESS" else 0.0
            GAUGE_LAST_TEST_STATUS.set(val)
            snapshot_values["dbrescue_last_test_status"] = val

            # Duration
            if run_row["started_at"] and run_row["completed_at"]:
                try:
                    s_dt = datetime.datetime.fromisoformat(run_row["started_at"])
                    c_dt = datetime.datetime.fromisoformat(run_row["completed_at"])
                    dur = max(0.0, (c_dt - s_dt).total_seconds())
                    GAUGE_LAST_TEST_DURATION.set(dur)
                    snapshot_values["dbrescue_last_test_duration_seconds"] = dur
                except Exception:
                    pass
        else:
            GAUGE_LAST_TEST_STATUS.set(-1.0)
            snapshot_values["dbrescue_last_test_status"] = -1.0

        # 2. Latest Backup Age
        cur = conn.execute(
            """
            SELECT created_at
            FROM backups
            WHERE status IN ('VALID', 'CREATED', 'VERIFIED_FILE', 'RESTORE_VERIFIED')
            ORDER BY created_at DESC LIMIT 1;
            """
        )
        b_row = cur.fetchone()
        if b_row and b_row["created_at"]:
            try:
                cleaned = b_row["created_at"].replace("Z", "+00:00")
                if "T" in cleaned:
                    b_dt = datetime.datetime.fromisoformat(cleaned)
                else:
                    b_dt = datetime.datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=datetime.UTC
                    )
                age_sec = max(0.0, (datetime.datetime.now(datetime.UTC) - b_dt).total_seconds())
                GAUGE_LAST_BACKUP_AGE.set(age_sec)
                snapshot_values["dbrescue_last_backup_age_seconds"] = age_sec
            except Exception:
                pass

        # 3. Latest Restore Parity
        cur = conn.execute(
            """
            SELECT verification_status
            FROM restores
            ORDER BY id DESC LIMIT 1;
            """
        )
        res_row = cur.fetchone()
        if res_row:
            r_val = 1.0 if res_row["verification_status"] == "VERIFIED" else 0.0
            GAUGE_LAST_RESTORE_VERIFIED.set(r_val)
            snapshot_values["dbrescue_last_restore_verified"] = r_val

        # 4. Baseline & Recovered Record Counts
        cur = conn.execute(
            """
            SELECT total_rows
            FROM snapshots
            WHERE is_baseline = 1
            ORDER BY id DESC LIMIT 1;
            """
        )
        base_snap = cur.fetchone()
        if base_snap:
            base_cnt = float(base_snap["total_rows"])
            GAUGE_RECORD_COUNT_BASELINE.set(base_cnt)
            snapshot_values["dbrescue_record_count_baseline"] = base_cnt

        cur = conn.execute(
            """
            SELECT rows_restored
            FROM restores
            ORDER BY id DESC LIMIT 1;
            """
        )
        restored_row = cur.fetchone()
        if restored_row and restored_row["rows_restored"] is not None:
            rec_cnt = float(restored_row["rows_restored"])
            GAUGE_RECORD_COUNT_RECOVERED.set(rec_cnt)
            snapshot_values["dbrescue_record_count_recovered"] = rec_cnt

        # 5. Active Alerts Count
        cur = conn.execute("SELECT COUNT(*) FROM alerts WHERE is_resolved = 0;")
        active_alerts = float(cur.fetchone()[0])
        GAUGE_ACTIVE_ALERTS.set(active_alerts)
        snapshot_values["dbrescue_active_alerts_count"] = active_alerts

    # 6. Live Telemetry Gauges (if provided)
    if telemetry:
        if telemetry.database.latency_ms is not None:
            GAUGE_DB_LATENCY.set(telemetry.database.latency_ms)
            snapshot_values["dbrescue_database_latency_ms"] = telemetry.database.latency_ms

        if telemetry.container.cpu_percent is not None:
            GAUGE_CONTAINER_CPU.set(telemetry.container.cpu_percent)
            snapshot_values["dbrescue_container_cpu_percent"] = telemetry.container.cpu_percent

        if telemetry.container.memory_usage_bytes is not None:
            mem_b = float(telemetry.container.memory_usage_bytes)
            GAUGE_CONTAINER_MEMORY.set(mem_b)
            snapshot_values["dbrescue_container_memory_bytes"] = mem_b

    return snapshot_values


def start_metrics_server(port: int = 9108, host: str = "127.0.0.1") -> None:
    """Launches Prometheus exposition server bound to loopback interface.

    Raises:
        MetricsExporterError: If server fails to bind or start.
    """
    try:
        start_http_server(port=port, addr=host)
        logger.info(
            "Prometheus metrics server successfully started on http://%s:%d/metrics", host, port
        )
    except Exception as err:
        raise MetricsExporterError(
            f"Failed to start Prometheus exporter on {host}:{port}: {err}"
        ) from err
