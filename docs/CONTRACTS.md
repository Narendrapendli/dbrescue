# Python Interfaces, Data Contracts & Metadata Schemas (`docs/CONTRACTS.md`)

This document defines the exact Python interfaces, data contracts, function signatures, typed dataclasses, custom exception hierarchies, and SQLite database schema specifications governing the `dbrescue` platform.

All modules conform strictly to Python 3.12+ type annotations, runtime validation policies, and defensive exception handling.

---

## 1. Core Enumerations & Base Types

### 1.1 Status Enum
Represents the discrete operational status of a run, container, backup, or audit operation:

```python
from enum import Enum

class Status(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    DEGRADED = "DEGRADED"
    SKIPPED = "SKIPPED"
```

### 1.2 RecoveryStage Enum (The 18 Workflow Steps)
Represents the deterministic state machine lifecycle of a disaster recovery run:

```python
class RecoveryStage(str, Enum):
    # Phase A: Pre-flight & Discovery
    INIT_RUN = "INIT_RUN"
    CHECK_DOCKER = "CHECK_DOCKER"
    LOCATE_VOLUME = "LOCATE_VOLUME"
    INSPECT_METADATA = "INSPECT_METADATA"
    
    # Phase B: Read-Only Storage Analysis
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
```

### 1.3 Base Exception Hierarchy
All platform exceptions inherit from `RescueException` to guarantee clean error interception:

```python
class RescueException(Exception):
    """Base exception for all dbrescue runtime and validation errors."""
    def __init__(self, message: str, stage: RecoveryStage | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.stage = stage
        self.details = details or {}
```

---

## 2. Module Interfaces & Contracts

### 2.1 Module: `dbrescue.config`
* **File**: `src/dbrescue/config.py`
* **Responsibilities**: Parsing `.env`, validating typed settings, validating loopback bindings (`127.0.0.1`), operational settings key-value access.
* **Owned SQLite Table**: `settings`

#### Dataclasses & Models
```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

@dataclass(frozen=True)
class MySQLConnectionConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    root_password: str
    pool_size: int = 5
    connect_timeout_sec: int = 10

@dataclass(frozen=True)
class DockerConfig:
    primary_container_name: str
    sandbox_container_name: str
    target_volume_name: str
    mysql_image: str = "mysql:8.4.3"
    exporter_image: str = "prom/mysqld-exporter:v0.16.0"
    sandbox_host_port: int = 3307

@dataclass(frozen=True)
class AppConfig:
    environment: str
    log_level: str
    sqlite_db_path: Path
    backup_dir: Path
    reports_dir: Path
    primary_mysql: MySQLConnectionConfig
    sandbox_mysql: MySQLConnectionConfig
    docker: DockerConfig
    prometheus_metrics_port: int = 9108
    streamlit_server_port: int = 8501
```

#### Exceptions
```python
class ConfigError(RescueException): pass
class MissingEnvironmentVariableError(ConfigError): pass
class InvalidPortBindingError(ConfigError): pass
```

#### Function Signatures
```python
def load_config(env_file_path: Optional[Path] = None) -> AppConfig:
    """Loads and validates configuration from environment and optional .env file.
    
    Raises:
        MissingEnvironmentVariableError: If mandatory security or credential vars are absent.
        InvalidPortBindingError: If MySQL hosts are bound to any address other than 127.0.0.1.
    """
    ...

def get_operational_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieves dynamic runtime parameter from the SQLite 'settings' table."""
    ...

def set_operational_setting(key: str, value: str, description: Optional[str] = None) -> None:
    """Persists dynamic runtime parameter into the SQLite 'settings' table."""
    ...
```

---

### 2.2 Module: `dbrescue.db`
* **File**: `src/dbrescue/db.py`
* **Responsibilities**: Connection pooling, query execution, health checks, parameterized statement execution against MySQL.

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int
    execution_time_ms: float
```

#### Exceptions
```python
class DatabaseError(RescueException): pass
class DatabaseConnectionError(DatabaseError): pass
class QueryExecutionError(DatabaseError): pass
class DatabaseAuthenticationError(DatabaseError): pass
class DatabaseNotFoundError(DatabaseError): pass
```

#### Function Signatures & Class Interfaces
```python
class MySQLConnectionPool:
    """Thread-safe connection pool wrapping mysql.connector.pooling.MySQLConnectionPool."""
    def __init__(self, config: MySQLConnectionConfig): ...
    def close(self) -> None: ...

def create_connection_pool(config: MySQLConnectionConfig) -> MySQLConnectionPool:
    """Creates a configured connection pool instance."""
    ...

def execute_query(pool: MySQLConnectionPool, sql: str, params: Optional[tuple] = None) -> QueryResult:
    """Executes a parameterized SQL query and returns tabular results with execution metrics.
    
    Raises:
        QueryExecutionError: If query execution fails or violates SQL syntax.
    """
    ...

def ping_database(config: MySQLConnectionConfig, timeout_sec: int = 5) -> bool:
    """Tests TCP connection and authentication against a target MySQL instance."""
    ...

def get_table_names(pool: MySQLConnectionPool, database_name: str) -> list[str]:
    """Retrieves all user table names in the specified database."""
    ...

def get_table_row_count(pool: MySQLConnectionPool, database_name: str, table_name: str) -> int:
    """Executes SELECT COUNT(*) using parameterized identifiers."""
    ...
```

---

### 2.3 Module: `dbrescue.docker_manager`
* **File**: `src/dbrescue/docker_manager.py`
* **Responsibilities**: Docker SDK management, volume attachment, container isolation, ephemeral inspector execution, strict loopback port binding.

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class ContainerInfo:
    container_id: str
    name: str
    status: str
    image: str
    created_at: str
    ports: dict[str, str]
    is_healthy: bool

@dataclass(frozen=True)
class VolumeInfo:
    name: str
    driver: str
    mountpoint: str
    labels: dict[str, str]
    created_at: str
```

#### Exceptions
```python
class DockerManagerError(RescueException): pass
class DockerDaemonUnreachableError(DockerManagerError): pass
class ContainerProvisionError(DockerManagerError): pass
class ContainerHealthTimeoutError(DockerManagerError): pass
class VolumeNotFoundError(DockerManagerError): pass
```

#### Class Interface
```python
class DockerManager:
    """Manages Docker engine communication, ephemeral inspector containers, and sandboxes."""
    def __init__(self, docker_config: DockerConfig): ...
    
    def ping_daemon(self) -> bool:
        """Pings the Docker daemon. Returns True if alive, False or raises DockerDaemonUnreachableError."""
        ...

    def get_volume(self, volume_name: str) -> VolumeInfo:
        """Inspects Docker volume by name. Raises VolumeNotFoundError if not found."""
        ...

    def run_ephemeral_inspector(self, volume_name: str, command: list[str], read_only: bool = True) -> str:
        """Mounts volume into a temporary alpine container, executes command, and returns stdout."""
        ...

    def provision_sandbox_container(
        self,
        volume_name: str,
        host_port: int = 3307,
        force_recovery_level: int = 0
    ) -> ContainerInfo:
        """Provisions an isolated MySQL 8.4 sandbox container bound strictly to 127.0.0.1:host_port."""
        ...

    def wait_for_container_health(self, container_id: str, timeout_sec: int = 60) -> bool:
        """Blocks until container passes Docker healthcheck or socket is open; raises ContainerHealthTimeoutError."""
        ...

    def stop_container(self, container_id: str, timeout_sec: int = 15) -> bool:
        """Gracefully stops a container with fallback kill."""
        ...

    def remove_container(self, container_id: str, force: bool = True) -> bool:
        """Removes a stopped container and associated anonymous volumes."""
        ...

    def get_container_logs(self, container_id: str, tail: int = 100) -> str:
        """Fetches trailing logs from container stdout/stderr."""
        ...
```

---

### 2.4 Module: `dbrescue.volume_inspector`
* **File**: `src/dbrescue/volume_inspector.py`
* **Responsibilities**: Physical file inspection of `/var/lib/mysql`, scanning `ibdata1`, `.ibd` tablespaces, detecting dirty shutdown flags and Log Sequence Numbers (LSN).

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class DataFileInfo:
    relative_path: str
    size_bytes: int
    modified_timestamp: float
    file_type: str  # 'SYSTEM_TABLESPACE', 'USER_TABLESPACE', 'REDO_LOG', 'UNDO_LOG', 'METADATA'

@dataclass(frozen=True)
class StorageLayoutReport:
    volume_name: str
    mountpoint: str
    total_size_bytes: int
    data_files: list[DataFileInfo]
    has_system_tablespace: bool
    has_redo_logs: bool
    detected_databases: list[str]

@dataclass(frozen=True)
class CorruptionScanResult:
    is_clean_shutdown: bool
    dirty_pages_detected: bool
    estimated_lsn: Optional[int]
    recommended_recovery_level: int  # 0 (normal) through 6 (maximum force recovery)
    warning_messages: list[str]
```

#### Exceptions
```python
class VolumeInspectorError(RescueException): pass
class InvalidStorageLayoutError(VolumeInspectorError): pass
class StorageAccessDeniedError(VolumeInspectorError): pass
class SevereCorruptionDetectedError(VolumeInspectorError): pass
```

#### Class Interface
```python
class VolumeInspector:
    """Inspects physical storage layouts and analyzes InnoDB file status safely."""
    def __init__(self, docker_manager: DockerManager): ...

    def inspect_volume(self, volume_name: str) -> StorageLayoutReport:
        """Scans volume structure and catalogues tablespaces, logs, and databases."""
        ...

    def scan_innodb_files(self, volume_name: str) -> list[DataFileInfo]:
        """Returns details for all InnoDB storage files inside the volume."""
        ...

    def check_corruption_markers(self, volume_name: str) -> CorruptionScanResult:
        """Parses ibdata1 and redo logs to detect unclean shutdown and recommend recovery levels."""
        ...

    def compute_raw_storage_fingerprint(self, volume_name: str) -> str:
        """Calculates a composite SHA256 fingerprint over all raw .ibd and ibdata1 file headers."""
        ...
```

---

### 2.5 Module: `dbrescue.integrity`
* **File**: `src/dbrescue/integrity.py`
* **Responsibilities**: Table validation, `CHECK TABLE ... EXTENDED`, row count tracking, data fingerprinting.
* **Owned SQLite Tables**: `snapshots`, `snapshot_rows`

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class TableAuditRecord:
    table_name: str
    row_count: int
    data_bytes: int
    table_checksum: str
    check_status: str  # 'OK', 'WARNING', 'CORRUPT'
    error_message: Optional[str] = None

@dataclass(frozen=True)
class IntegrityReport:
    database_name: str
    audited_at: str
    total_tables: int
    total_rows: int
    total_bytes: int
    fingerprint_hash: str
    table_records: list[TableAuditRecord]
    passed: bool
```

#### Exceptions
```python
class IntegrityError(RescueException): pass
class TableCorruptionError(IntegrityError): pass
class SnapshotNotFoundError(IntegrityError): pass
class SchemaMismatchError(IntegrityError): pass
```

#### Class Interface
```python
class IntegrityEngine:
    """Audits database logical structures, executes check routines, and generates snapshots."""
    def __init__(self, sqlite_db_path: Path): ...

    def audit_database_integrity(self, pool: MySQLConnectionPool, database_name: str) -> IntegrityReport:
        """Runs CHECK TABLE EXTENDED and calculates row counts and checksums across all tables."""
        ...

    def compute_table_fingerprint(self, pool: MySQLConnectionPool, database_name: str, table_name: str) -> str:
        """Computes deterministic SHA256 checksum across sorted primary key tuples."""
        ...

    def capture_database_snapshot(
        self,
        pool: MySQLConnectionPool,
        database_name: str,
        run_id: int,
        is_baseline: bool = False
    ) -> int:
        """Persists snapshot header and per-table records into SQLite. Returns snapshot_id."""
        ...

    def compare_snapshots(self, baseline_snapshot_id: int, target_snapshot_id: int) -> dict[str, Any]:
        """Compares two snapshots and returns row count deltas, missing tables, and checksum diffs."""
        ...
```

---

### 2.6 Module: `dbrescue.recovery`
* **File**: `src/dbrescue/recovery.py`
* **Responsibilities**: Orchestrates the 18-stage disaster recovery state machine, handles step transitions, rollbacks, and run status.
* **Owned SQLite Table**: `runs`

#### Dataclasses & Models
```python
@dataclass
class RecoveryContext:
    run_id: int
    run_uuid: str
    target_volume: str
    trigger_type: str
    primary_container: str
    sandbox_container: Optional[str] = None
    current_stage: RecoveryStage = RecoveryStage.INIT_RUN
    status: Status = Status.PENDING
    started_at: str = ""
    completed_at: Optional[str] = None
    force_recovery_level: int = 0
    error_message: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class RunResult:
    run_id: int
    run_uuid: str
    status: Status
    target_volume: str
    initial_stage: RecoveryStage
    final_stage: RecoveryStage
    duration_seconds: float
    error_message: Optional[str]
    backup_path: Optional[Path]
    report_path: Optional[Path]
```

#### Exceptions
```python
class RecoveryWorkflowError(RescueException): pass
class StageTransitionError(RecoveryWorkflowError): pass
class RollbackFailedError(RecoveryWorkflowError): pass
class RecoveryAbortedError(RecoveryWorkflowError): pass
```

#### Class Interface
```python
class RecoveryOrchestrator:
    """Deterministic orchestrator managing the 18-stage recovery state machine."""
    def __init__(
        self,
        config: AppConfig,
        docker_manager: DockerManager,
        volume_inspector: VolumeInspector,
        integrity_engine: IntegrityEngine,
        backup_manager: Any,  # BackupManager
        audit_logger: Any,    # AuditLogger
        alert_manager: Any,   # AlertManager
        metrics_collector: Any # MetricsCollector
    ): ...

    def execute_recovery(
        self,
        target_volume: str,
        trigger: str = "MANUAL_CLI",
        baseline_snapshot_id: Optional[int] = None
    ) -> RunResult:
        """Executes the complete 18-stage recovery workflow with rollback protection."""
        ...

    def transition_stage(self, context: RecoveryContext, next_stage: RecoveryStage) -> None:
        """Transitions state machine to next stage, logging audit records and updating SQLite 'runs'."""
        ...

    def rollback_recovery(self, context: RecoveryContext, reason: str) -> None:
        """Safely tears down ephemeral containers and marks run status as FAILED or ABORTED."""
        ...

    def get_run_status(self, run_uuid: str) -> Optional[RunResult]:
        """Queries the current run status by run_uuid."""
        ...

    def list_recent_runs(self, limit: int = 20) -> list[RunResult]:
        """Returns the most recent runs ordered by started_at descending."""
        ...
```

---

### 2.7 Module: `dbrescue.backup`
* **File**: `src/dbrescue/backup.py`
* **Responsibilities**: Executes `mysqldump` with `--single-transaction`, streams gzip compression, computes SHA256 checksums.
* **Owned SQLite Table**: `backups`

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class BackupMetadata:
    backup_id: int
    run_id: Optional[int]
    filename: str
    file_path: Path
    file_size_bytes: int
    sha256_checksum: str
    compression_type: str
    database_name: str
    total_tables: int
    total_rows: int
    status: str  # 'VALID', 'CORRUPT', 'EXPIRED'
    created_at: str
```

#### Exceptions
```python
class BackupError(RescueException): pass
class DumpProcessFailedError(BackupError): pass
class BackupChecksumMismatchError(BackupError): pass
class BackupStorageFullError(BackupError): pass
```

#### Class Interface
```python
class BackupManager:
    """Manages creation, hashing, and verification of compressed MySQL logical dumps."""
    def __init__(self, backup_dir: Path, sqlite_db_path: Path): ...

    def create_logical_backup(
        self,
        config: MySQLConnectionConfig,
        database_name: str,
        run_id: Optional[int] = None,
        total_tables: int = 0,
        total_rows: int = 0
    ) -> BackupMetadata:
        """Dumps database using mysqldump with single-transaction, compresses via gzip, and hashes with SHA256."""
        ...

    def verify_backup_checksum(self, backup_path: Path, expected_sha256: str) -> bool:
        """Streams backup artifact through SHA256 hasher and validates match."""
        ...

    def list_backups(self, database_name: Optional[str] = None) -> list[BackupMetadata]:
        """Lists registered backups from the SQLite 'backups' table."""
        ...

    def delete_backup(self, backup_id: int) -> bool:
        """Deletes backup artifact from disk and marks record as EXPIRED in SQLite."""
        ...
```

---

### 2.8 Module: `dbrescue.restore`
* **File**: `src/dbrescue/restore.py`
* **Responsibilities**: Archive ingestion, dry-run simulation, checksum validation, post-restore parity checks.
* **Owned SQLite Table**: `restores`

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class RestoreResult:
    restore_id: int
    run_id: int
    backup_id: int
    target_database: str
    status: Status
    rows_restored: int
    verification_status: str  # 'VERIFIED', 'MISMATCH', 'UNVERIFIED'
    duration_seconds: float
    error_message: Optional[str] = None
```

#### Exceptions
```python
class RestoreError(RescueException): pass
class CorruptBackupArchiveError(RestoreError): pass
class RestoreExecutionFailedError(RestoreError): pass
class PostRestoreParityError(RestoreError): pass
```

#### Class Interface
```python
class RestoreEngine:
    """Restores database from validated compressed backup archives."""
    def __init__(self, sqlite_db_path: Path): ...

    def simulate_restore_dry_run(
        self,
        backup_id: int,
        target_config: MySQLConnectionConfig
    ) -> bool:
        """Validates archive integrity and tests target connectivity without applying DDL/DML."""
        ...

    def execute_restore(
        self,
        backup_id: int,
        target_config: MySQLConnectionConfig,
        run_id: int
    ) -> RestoreResult:
        """Streams uncompressed SQL into target MySQL instance within a transaction."""
        ...

    def verify_post_restore_parity(
        self,
        backup_id: int,
        target_config: MySQLConnectionConfig,
        pool: MySQLConnectionPool
    ) -> bool:
        """Verifies restored table row counts match the backup metadata."""
        ...
```

---

### 2.9 Module: `dbrescue.monitoring`
* **File**: `src/dbrescue/monitoring.py`
* **Responsibilities**: Prometheus metrics definitions, stage duration histogram, export endpoint (`127.0.0.1:9108/metrics`).
* **Owned SQLite Table**: `metrics_samples`

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class MetricsSample:
    sample_id: int
    run_id: int
    timestamp: str
    metric_name: str
    metric_value: float
    labels: dict[str, str]
```

#### Exceptions
```python
class MonitoringError(RescueException): pass
class MetricsServerPortConflictError(MonitoringError): pass
class TelemetryCollectionError(MonitoringError): pass
```

#### Class Interface
```python
class MetricsCollector:
    """Instruments dbrescue workflows with Prometheus counters, gauges, and histograms."""
    def __init__(self, sqlite_db_path: Path): ...

    def record_stage_duration(self, stage: RecoveryStage, duration_seconds: float) -> None:
        """Records the execution duration of a recovery stage into Prometheus histogram."""
        ...

    def increment_run_counter(self, status: Status, trigger: str) -> None:
        """Increments the run completion counter categorized by status and trigger type."""
        ...

    def record_table_discrepancy(self, table_name: str, delta: int) -> None:
        """Updates gauge of table row count discrepancies."""
        ...

    def persist_metrics_sample(self, run_id: int, metric_name: str, value: float, labels: dict[str, str]) -> None:
        """Stores a metric sample in the SQLite 'metrics_samples' table."""
        ...

    def start_metrics_server(self, port: int = 9108) -> None:
        """Starts Prometheus HTTP exposition server on 127.0.0.1:port."""
        ...
```

---

### 2.10 Module: `dbrescue.alerts`
* **File**: `src/dbrescue/alerts.py`
* **Responsibilities**: Alert rule evaluations, severity dispatch, tracking unresolved alerts.
* **Owned SQLite Table**: `alerts`

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class AlertRecord:
    alert_id: int
    run_id: Optional[int]
    alert_name: str
    severity: str  # 'INFO', 'WARNING', 'CRITICAL'
    message: str
    is_resolved: bool
    resolved_at: Optional[str]
    created_at: str
```

#### Exceptions
```python
class AlertError(RescueException): pass
class AlertDispatchFailedError(AlertError): pass
```

#### Class Interface
```python
class AlertManager:
    """Evaluates operational alerts and records them in SQLite."""
    def __init__(self, sqlite_db_path: Path): ...

    def emit_alert(
        self,
        alert_name: str,
        severity: str,
        message: str,
        run_id: Optional[int] = None
    ) -> AlertRecord:
        """Persists alert record into SQLite 'alerts' table and logs to stderr."""
        ...

    def resolve_alert(self, alert_id: int) -> bool:
        """Marks an alert as resolved with current timestamp."""
        ...

    def list_active_alerts(self) -> list[AlertRecord]:
        """Returns all unresolved alerts."""
        ...

    def evaluate_integrity_rules(self, report: IntegrityReport, run_id: int) -> list[AlertRecord]:
        """Evaluates report against discrepancy rules and emits alerts for corrupt tables."""
        ...
```

---

### 2.11 Module: `dbrescue.audit`
* **File**: `src/dbrescue/audit.py`
* **Responsibilities**: Append-only audit logger, forensic compliance event tracking.
* **Owned SQLite Table**: `audit_events`

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class AuditEvent:
    event_id: int
    run_id: int
    stage: RecoveryStage
    event_type: str
    severity: str
    message: str
    details: dict[str, Any]
    created_at: str
```

#### Exceptions
```python
class AuditError(RescueException): pass
class AuditLogWriteError(AuditError): pass
```

#### Class Interface
```python
class AuditLogger:
    """Maintains an append-only, tamper-evident operational audit trail in SQLite."""
    def __init__(self, sqlite_db_path: Path): ...

    def log_event(
        self,
        run_id: int,
        stage: RecoveryStage,
        event_type: str,
        severity: str,
        message: str,
        details: Optional[dict[str, Any]] = None
    ) -> AuditEvent:
        """Appends a new audit record into SQLite 'audit_events'."""
        ...

    def get_run_audit_trail(self, run_id: int) -> list[AuditEvent]:
        """Retrieves ordered sequence of audit events for a given run."""
        ...

    def export_audit_log_json(self, run_id: int, output_path: Path) -> Path:
        """Exports audit records to a structured JSON file."""
        ...
```

---

### 2.12 Module: `dbrescue.reports`
* **File**: `src/dbrescue/reports.py`
* **Responsibilities**: Compiling production-grade vector PDF disaster recovery certificates and reports using ReportLab.

#### Dataclasses & Models
```python
@dataclass(frozen=True)
class ReportSection:
    title: str
    content: str
    status_indicator: Status
    tables_data: Optional[list[list[str]]] = None
```

#### Exceptions
```python
class ReportGenerationError(RescueException): pass
class PDFCompilationError(ReportGenerationError): pass
class ReportDataIncompleteError(ReportGenerationError): pass
```

#### Class Interface
```python
class RecoveryReportGenerator:
    """Compiles audit and recovery records into certified PDF documents via ReportLab."""
    def __init__(self, sqlite_db_path: Path, reports_dir: Path): ...

    def compile_pdf_report(self, run_uuid: str, output_path: Optional[Path] = None) -> Path:
        """Renders comprehensive multi-page PDF report with metadata, 18-stage timeline, and checksums."""
        ...

    def build_executive_summary(self, run_result: RunResult) -> str:
        """Generates plain text executive summary of the disaster recovery run."""
        ...

    def generate_integrity_certificate(self, run_uuid: str) -> Path:
        """Generates single-page cryptographically signed data integrity verification certificate."""
        ...
```

---

## 3. SQLite Metadata Schema DDL & Constraints

The SQLite database (`operational.db`) serves as the operational system of record. Foreign key support is strictly enforced:

```sql
PRAGMA foreign_keys = ON;

-- Table 1: runs (Owned by dbrescue.recovery)
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid TEXT NOT NULL UNIQUE,
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('MANUAL_UI', 'MANUAL_CLI', 'SCHEDULED', 'ALERT_TRIGGERED')),
    target_volume TEXT NOT NULL,
    primary_container TEXT NOT NULL,
    sandbox_container TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'ABORTED', 'DEGRADED', 'SKIPPED')),
    current_stage TEXT NOT NULL,
    initial_stage TEXT NOT NULL,
    final_stage TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Table 2: snapshots (Owned by dbrescue.integrity)
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    database_name TEXT NOT NULL,
    total_tables INTEGER NOT NULL,
    total_rows INTEGER NOT NULL,
    total_data_bytes INTEGER NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    is_baseline BOOLEAN NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Table 3: snapshot_rows (Owned by dbrescue.integrity)
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

-- Table 4: backups (Owned by dbrescue.backup)
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    filename TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    sha256_checksum TEXT NOT NULL,
    compression_type TEXT NOT NULL CHECK(compression_type IN ('GZIP', 'NONE')),
    database_name TEXT NOT NULL,
    total_tables INTEGER NOT NULL,
    total_rows INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('VALID', 'CORRUPT', 'EXPIRED')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Table 5: restores (Owned by dbrescue.restore)
CREATE TABLE IF NOT EXISTS restores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    backup_id INTEGER NOT NULL REFERENCES backups(id) ON DELETE RESTRICT,
    target_database TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('SUCCESS', 'FAILED', 'IN_PROGRESS')),
    rows_restored INTEGER DEFAULT 0,
    verification_status TEXT NOT NULL CHECK(verification_status IN ('VERIFIED', 'MISMATCH', 'UNVERIFIED')),
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Table 6: audit_events (Owned by dbrescue.audit)
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    message TEXT NOT NULL,
    details_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Table 7: alerts (Owned by dbrescue.alerts)
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    alert_name TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL')),
    message TEXT NOT NULL,
    is_resolved BOOLEAN NOT NULL DEFAULT 0,
    resolved_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Table 8: settings (Owned by dbrescue.config)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Table 9: metrics_samples (Owned by dbrescue.monitoring)
CREATE TABLE IF NOT EXISTS metrics_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    labels_json TEXT
);
```

---

## 4. Module Ownership & Data Access Matrix

To prevent data corruption, concurrency deadlocks, and cross-boundary coupling, every SQLite table has a single owning module responsible for schema migrations and writes:

| SQLite Table Name | Primary Owner Module | Write Policy | Permitted Readers | Business Scope |
| :--- | :--- | :--- | :--- | :--- |
| **`runs`** | `dbrescue.recovery` | Exclusive writes | `reports`, `audit`, `monitoring`, `app` | Orchestration lifecycle tracking and run metadata. |
| **`snapshots`** | `dbrescue.integrity` | Exclusive writes | `recovery`, `reports`, `app` | High-level database state signatures and timestamps. |
| **`snapshot_rows`** | `dbrescue.integrity` | Exclusive writes | `reports`, `alerts`, `app` | Per-table row counts, storage bytes, and check statuses. |
| **`backups`** | `dbrescue.backup` | Exclusive writes | `restore`, `recovery`, `reports`, `app` | Registry of logical dump files, sizes, and SHA256 hashes. |
| **`restores`** | `dbrescue.restore` | Exclusive writes | `recovery`, `reports`, `app` | History of restore operations, target DBs, and parity checks. |
| **`audit_events`** | `dbrescue.audit` | Append-only | `recovery`, `reports`, `app` | Tamper-evident forensic operational event log. |
| **`alerts`** | `dbrescue.alerts` | Exclusive writes | `recovery`, `monitoring`, `app` | Active and historical incident alerts. |
| **`settings`** | `dbrescue.config` | Exclusive writes | All modules | Dynamic operational configurations and runtime overrides. |
| **`metrics_samples`**| `dbrescue.monitoring`| Exclusive writes | `reports`, `app` | Time-series operational telemetry samples. |
