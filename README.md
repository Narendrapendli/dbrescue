# dbrescue: Docker MySQL 8.4 LTS Disaster Recovery & Integrity Auditing Platform

[![CI Pipeline](https://github.com/Narendrapendli/dbrescue/actions/workflows/ci.yml/badge.svg)](https://github.com/Narendrapendli/dbrescue/actions/workflows/ci.yml)
[![Python 3.11 | 3.12 | 3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Docker: MySQL 8.4 LTS](https://img.shields.io/badge/docker-MySQL%208.4%20LTS-00758F.svg)](https://hub.docker.com/_/mysql)
[![Test Suite Pass](https://img.shields.io/badge/tests-133%20passed-10B981.svg)](docs/TEST_REPORT.md)
[![Backend Coverage](https://img.shields.io/badge/coverage-80.0%25-brightgreen.svg)](docs/TEST_REPORT.md)

An automated disaster recovery, cryptographic integrity auditing, and observability dashboard for MySQL 8.4 LTS running in Docker. Validates storage volume persistence across container destruction, verifies zero data loss via row-level SHA-256 fingerprinting, streams logical backups with cryptographic parity checks, and provides an operational control plane.

---

## Key Capabilities

1. **18-Stage Disaster Recovery State Machine**:
   - Executes deterministic container recreation while strictly preserving named volumes (`mysql_data`).
   - Validates baseline vs. recovered row counts and deterministic SHA-256 table fingerprints.
   - Requires explicit typed confirmation tokens (`CONFIRM-CONTAINER-NAME`) before any destructive action.
2. **Cryptographic Integrity & Verification**:
   - Computes deterministic SHA-256 fingerprints across database tables.
   - Detects missing rows, inserted rows, and tampered column values.
3. **Backup, Verification & Isolated Restore**:
   - Streams `mysqldump` archives with GZIP compression and computes inline SHA-256 checksums without writing credentials to the CLI.
   - Performs isolated dry-run restores into temporary sandbox databases (`dbrescue_restore_<id>`), verifying 100% row and fingerprint parity before dropping the sandbox.
   - Enforces configurable retention policies while permanently protecting the newest verified backup.
4. **Real-Time Observability & Evidence-Based Alerting**:
   - Monitors live query latency, active connections, and container CPU/memory via Docker Stats API.
   - Rules remain in `NOT_EVALUATED` state until real telemetry is recorded (never displays false "OK" status).
5. **Streamlit Operational Dashboard**:
   - Dark navy theme with responsive KPI cards, 18-stage workflow stepper, forensic audit explorer, and ReportLab PDF certificate generator.

---

## Safety Invariant Guarantee

> **CRITICAL RULE**: `docker compose down -v`, `docker volume rm`, or volume pruning is **STRICTLY PROHIBITED**. Persistent storage volumes must outlive container lifecycles under all operational conditions.

---

## Quickstart

### Prerequisites
- Python 3.11, 3.12, or 3.13
- Docker Desktop with Docker Compose v2
- Git

### Linux / macOS (`Makefile`)
```bash
# Setup virtual environment and dependencies
make setup

# Start MySQL 8.4 LTS container (preserves volume)
make up

# Seed database with initial fixtures
make seed

# Run automated tests
make test

# Launch operational dashboard
make run-app
```

### Windows PowerShell (`tasks.ps1`)
```powershell
# Setup virtual environment and dependencies
.\tasks.ps1 setup

# Start MySQL 8.4 LTS container (preserves volume)
.\tasks.ps1 up

# Seed database with initial fixtures
.\tasks.ps1 seed

# Run automated tests
.\tasks.ps1 test

# Launch operational dashboard
.\tasks.ps1 run-app
```

The dashboard will be available at `http://127.0.0.1:8501`.

---

## Testing & Quality Assurance

- **Unit, Error, Security & Integration Suites**:
  ```bash
  pytest tests/ -v
  ```
- **Acceptance Test Report Generator**:
  ```bash
  python scripts/make_test_report.py
  ```
  Generates [docs/TEST_REPORT.md](docs/TEST_REPORT.md) from real test runs.
- **Code Linting & Formatting**:
  ```bash
  ruff check .
  ruff format --check .
  ```
