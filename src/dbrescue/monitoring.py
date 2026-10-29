"""Operational telemetry collection, real-time performance instrumentation, and freshness auditing.

Collects real metrics without fabrication:
  - Database connectivity and microsecond latency via real timed queries
  - Container lifecycle state and Docker stats (CPU %, memory bytes, or UNKNOWN if unavailable)
  - Active threads connected from MySQL SHOW STATUS
  - Volume mount and storage status
  - Freshness evaluation (FRESH, STALE, UNKNOWN) based on sample age
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import mysql.connector

from dbrescue.audit import AuditLogger
from dbrescue.config import AppConfig, MySQLConnectionConfig, RescueException
from dbrescue.docker_manager import DockerManager
from dbrescue.volume_inspector import VolumeInspector

logger = logging.getLogger("dbrescue.monitoring")


# --- Freshness & Status Enums ---

class FreshnessStatus(str, Enum):
    """Categorical freshness indicator for operational metrics."""
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


# --- Exceptions ---

class MonitoringError(RescueException):
    """Base exception for monitoring subsystem failures."""
    pass


class TelemetryCollectionError(MonitoringError):
    """Raised when collecting metrics encounters an unhandled system failure."""
    pass


# --- Telemetry Data Models ---

@dataclass(frozen=True)
class ContainerMetrics:
    """Real container state and resource consumption metrics."""
    state: str = "UNKNOWN"  # e.g. "running", "exited", "UNKNOWN"
    cpu_percent: float | None = None  # None indicates UNKNOWN (never false 0.0)
    memory_usage_bytes: int | None = None
    memory_limit_bytes: int | None = None
    memory_percent: float | None = None

    @property
    def is_running(self) -> bool:
        return self.state.lower() == "running"


@dataclass(frozen=True)
class DatabaseMetrics:
    """Real MySQL server telemetry and query latency."""
    is_connected: bool = False
    latency_ms: float | None = None  # None indicates UNKNOWN or unreachable
    threads_connected: int | None = None
    server_version: str | None = None
    uptime_seconds: int | None = None


@dataclass(frozen=True)
class VolumeMetrics:
    """Storage volume status and allocation telemetry."""
    exists: bool = False
    is_mounted: bool = False
    mountpoint: str | None = None
    size_display: str = "UNKNOWN"


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Unified telemetry capture representing host, engine, and storage at a point in time."""
    timestamp_utc: str
    timestamp_mono: float
    container: ContainerMetrics
    database: DatabaseMetrics
    volume: VolumeMetrics
    freshness: FreshnessStatus = FreshnessStatus.FRESH
    age_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# --- Monitoring Collector ---

class MonitoringCollector:
    """Gathers real-time performance, container, and database metrics."""

    def __init__(
        self,
        sqlite_db_path: Path | str,
        config: AppConfig | None = None,
        docker_manager: DockerManager | None = None,
        volume_inspector: VolumeInspector | None = None,
        freshness_threshold_sec: float = 60.0,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.sqlite_db_path = Path(sqlite_db_path).resolve()
        self.config = config
        self.docker_manager = docker_manager
        self.volume_inspector = volume_inspector
        self.freshness_threshold_sec = freshness_threshold_sec
        self.audit_logger = audit_logger or AuditLogger(self.sqlite_db_path)
        self._last_snapshot: TelemetrySnapshot | None = None

    def measure_db_latency(self, db_config: MySQLConnectionConfig) -> tuple[bool, float | None, int | None]:
        """Executes a real timed query to measure latency and retrieve threads_connected.

        Returns:
            (is_connected, latency_ms, threads_connected)
        """
        start = time.perf_counter()
        try:
            conn = mysql.connector.connect(
                host=db_config.host,
                port=db_config.port,
                user=db_config.user,
                password=db_config.password,
                database=db_config.database,
                connect_timeout=min(db_config.connect_timeout_sec, 3),
            )
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchall()
            latency_ms = (time.perf_counter() - start) * 1000.0

            # Retrieve active threads connected
            threads_connected: int | None = None
            with contextlib.suppress(Exception):
                cursor.execute("SHOW STATUS LIKE 'Threads_connected';")
                row = cursor.fetchone()
                if row and row[1]:
                    threads_connected = int(row[1])

            cursor.close()
            conn.close()
            return True, round(latency_ms, 2), threads_connected
        except Exception:
            return False, None, None

    def collect_container_stats(self, container_name: str) -> ContainerMetrics:
        """Inspects real container state and queries Docker stats API if running.

        Returns UNKNOWN (None) values when Docker or stats are unavailable.
        """
        if not self.docker_manager:
            return ContainerMetrics()

        try:
            state = self.docker_manager.get_container_state(container_name)
            if not state or state.status.lower() != "running":
                return ContainerMetrics(state=state.status if state else "UNKNOWN")

            # Collect CPU & Memory from Docker stats API (single stream=False sample)
            client = self.docker_manager._get_client()
            container = client.containers.get(container_name)
            stats = container.stats(stream=False)

            cpu_pct: float | None = None
            mem_usage: int | None = None
            mem_limit: int | None = None
            mem_pct: float | None = None

            # Calculate CPU percentage according to official Docker formula
            cpu_stats = stats.get("cpu_stats", {})
            precpu_stats = stats.get("precpu_stats", {})
            cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
            system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
            online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage") or [1])

            if system_delta > 0 and cpu_delta > 0:
                cpu_pct = round((cpu_delta / system_delta) * online_cpus * 100.0, 2)

            # Memory metrics
            memory_stats = stats.get("memory_stats", {})
            if "usage" in memory_stats:
                mem_usage = memory_stats.get("usage", 0)
                mem_limit = memory_stats.get("limit", 0)
                if mem_limit > 0:
                    mem_pct = round((mem_usage / mem_limit) * 100.0, 2)

            return ContainerMetrics(
                state=state.status,
                cpu_percent=cpu_pct,
                memory_usage_bytes=mem_usage,
                memory_limit_bytes=mem_limit,
                memory_percent=mem_pct,
            )
        except Exception as err:
            logger.debug("Container stats collection encountered error: %s", err)
            return ContainerMetrics(state="UNKNOWN")

    def collect_volume_status(self, volume_name: str, container_name: str) -> VolumeMetrics:
        """Inspects physical existence and mount state of MySQL volume."""
        if not self.volume_inspector:
            return VolumeMetrics()

        try:
            vol_info = self.volume_inspector.inspect_volume(volume_name)
            is_mounted = False

            if self.docker_manager:
                mounts = self.volume_inspector.inspect_container_mounts(container_name)
                is_mounted = any(m.source == volume_name for m in mounts)

            return VolumeMetrics(
                exists=True,
                is_mounted=is_mounted,
                mountpoint=vol_info.mountpoint,
                size_display=vol_info.size_display,
            )
        except Exception:
            return VolumeMetrics(exists=False, is_mounted=False, size_display="UNKNOWN")

    def collect_telemetry(self) -> TelemetrySnapshot:
        """Gathers real metrics across container, database, and storage without synthetic mock data."""
        now_utc = datetime.datetime.now(datetime.UTC).isoformat()
        mono = time.perf_counter()

        # 1. Database telemetry
        db_metrics = DatabaseMetrics()
        if self.config:
            connected, latency_ms, threads = self.measure_db_latency(self.config.mysql)
            db_metrics = DatabaseMetrics(
                is_connected=connected,
                latency_ms=latency_ms,
                threads_connected=threads,
            )

        # 2. Container telemetry
        container_name = self.config.docker.primary_container_name if self.config else "mysql_prod"
        container_metrics = self.collect_container_stats(container_name)

        # 3. Volume telemetry
        volume_name = self.config.docker.volume_name if self.config else "mysql_data"
        volume_metrics = self.collect_volume_status(volume_name, container_name)

        snapshot = TelemetrySnapshot(
            timestamp_utc=now_utc,
            timestamp_mono=mono,
            container=container_metrics,
            database=db_metrics,
            volume=volume_metrics,
            freshness=FreshnessStatus.FRESH,
            age_seconds=0.0,
        )

        self._last_snapshot = snapshot
        self._persist_snapshot_samples(snapshot)
        return snapshot

    def get_telemetry(self, force_refresh: bool = False) -> TelemetrySnapshot:
        """Returns the most recent telemetry snapshot with real-time freshness calculation.

        If no snapshot exists or force_refresh is True, triggers a fresh collection.
        Marks STALE if sample age exceeds freshness_threshold_sec.
        """
        if self._last_snapshot is None or force_refresh:
            return self.collect_telemetry()

        elapsed = time.perf_counter() - self._last_snapshot.timestamp_mono
        is_fresh = elapsed < self.freshness_threshold_sec

        freshness = FreshnessStatus.FRESH if is_fresh else FreshnessStatus.STALE
        return TelemetrySnapshot(
            timestamp_utc=self._last_snapshot.timestamp_utc,
            timestamp_mono=self._last_snapshot.timestamp_mono,
            container=self._last_snapshot.container,
            database=self._last_snapshot.database,
            volume=self._last_snapshot.volume,
            freshness=freshness,
            age_seconds=round(elapsed, 1),
            metadata=self._last_snapshot.metadata,
        )

    def _persist_snapshot_samples(self, snapshot: TelemetrySnapshot) -> None:
        """Persists discrete metrics into SQLite metrics_samples table."""
        samples: list[tuple[str, float, dict[str, str]]] = []

        if snapshot.database.latency_ms is not None:
            samples.append(("db_query_latency_ms", snapshot.database.latency_ms, {"component": "mysql"}))

        if snapshot.database.threads_connected is not None:
            samples.append(("db_threads_connected", float(snapshot.database.threads_connected), {"component": "mysql"}))

        if snapshot.container.cpu_percent is not None:
            samples.append(("container_cpu_percent", snapshot.container.cpu_percent, {"component": "docker"}))

        if snapshot.container.memory_usage_bytes is not None:
            samples.append(("container_memory_bytes", float(snapshot.container.memory_usage_bytes), {"component": "docker"}))

        if not samples:
            return

        with self.audit_logger._get_connection() as conn:
            for name, val, labels in samples:
                conn.execute(
                    """
                    INSERT INTO metrics_samples (run_id, timestamp, metric_name, metric_value, labels_json)
                    VALUES (NULL, ?, ?, ?, ?);
                    """,
                    (snapshot.timestamp_utc, name, val, json.dumps(labels)),
                )
            conn.commit()
