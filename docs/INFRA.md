# Infrastructure, Containerization & Storage Specification (`docs/INFRA.md`)

This document defines the containerized infrastructure, volume storage layouts, init-script lifecycle rules, and database schema specifications for the **dbrescue** MySQL 8.4 LTS disaster recovery platform.

---

## 1. Container Architecture & Docker Compose Overview

The primary infrastructure is defined declaratively in [`compose.yaml`](file:///c:/Users/Narendra/Downloads/AWS%20PROJECT/compose.yaml) and features:
1. **Pinned MySQL 8.4 LTS Service (`mysql`)**: Runs Oracle Linux-based `mysql:8.4.3` with hardened InnoDB parameters and UTF-8 multibyte encoding (`utf8mb4`).
2. **Loopback Port Isolation**: All exposed MySQL listener ports are strictly bound to `127.0.0.1:${MYSQL_PRIMARY_PORT:-3306}:3306`. Exposure to `0.0.0.0` or public network interfaces is forbidden.
3. **Dedicated Named Volume (`mysql_data`)**: Persistent MySQL data directory `/var/lib/mysql` mounted to named Docker volume `dbrescue_mysql_data`.
4. **Independent Backup Mount**: `./backups` is mounted separately to `/backups`, ensuring disaster recovery archives are stored physically distinct from the database tablespace volume.
5. **Deterministic Healthcheck**: Validates socket responsiveness using `mysqladmin ping -h 127.0.0.1 -u root -p${MYSQL_ROOT_PASSWORD}` with a 30-second start grace period.
6. **Deploy Resource Limits**: Capped at 2.0 CPUs and 2048MB memory to prevent resource starvation on the host during chaos recovery tests.

---

## 2. Docker Entrypoint & Init-Script Lifecycle

> [!IMPORTANT]
> **MySQL Official Entrypoint Init-Script Behavior:**
> The initialization scripts placed inside `./db/init` (mounted to `/docker-entrypoint-initdb.d` inside the container) are executed **ONLY when the data directory `/var/lib/mysql` is completely empty (first initialization)**.

### Detailed Lifecycle Rules:
1. **Fresh Volume Startup**:
   - When a new named volume (`dbrescue_mysql_data`) is created, the MySQL entrypoint initializes system tables in `/var/lib/mysql`.
   - Once system tables are ready, the entrypoint executes all `.sql`, `.sql.gz`, and `.sh` files in `/docker-entrypoint-initdb.d/` in alphabetical order:
     - `01_schema.sql`: Creates databases (`student_db`, `dbrescue_test`) and tables (`students`, `courses`, `enrollments`, `test_records`).
     - `02_users.sql`: Provisions least-privilege users (`rescue_app`, `rescue_backup`, `rescue_restore`).
   - The temporary MySQL initialization daemon shuts down, and the persistent daemon starts up with the new schema in place.

2. **Subsequent Container Restarts**:
   - If `/var/lib/mysql` already contains initialized database files (e.g. `ibdata1`, system schema, or `auto.cnf`), **the entrypoint skips `/docker-entrypoint-initdb.d/` entirely**.
   - Changes to files in `./db/init/` will **NOT** be automatically applied to existing volumes.

3. **Applying Schema Updates to Existing Volumes**:
   - To apply migrations or modifications to existing data volumes, use the `scripts/db-seed.ps1` (or `scripts/db-seed.sh`) utility scripts or direct client execution.
   - **SAFETY RULE**: Developer and operational scripts are strictly forbidden from running `docker compose down -v` or removing volumes, ensuring zero inadvertent data loss.

---

## 3. Database Schemas & Table Layout

### 3.1 Primary Business Database: `student_db`
Designed to represent an academic database with real-world relational constraints, indexes, and primary keys:

| Table Name | Primary Key | Foreign Keys | Key Indexes | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`students`** | `student_id` (BIGINT) | None | `student_uuid` (UQ), `email` (UQ), `status`, `enrolled_date` | 100+ synthetic student records with GPAs, majors, and status flags. |
| **`courses`** | `course_id` (BIGINT) | None | `course_code` (UQ), `department`, `is_active` | Catalog of courses across STEM departments with credit hours. |
| **`enrollments`** | `enrollment_id` (BIGINT) | `fk_enrollments_student` -> `students.student_id`<br>`fk_enrollments_course` -> `courses.course_id` | `(student_id, course_id, semester)` (UQ), `semester` | Relational join mapping student enrollments, grades, and completion statuses. |
| **`test_records`** | `record_id` (BIGINT) | None | `run_id`, `marker_name` | Operational markers tagged by `run_id` for deterministic recovery validation. |

### 3.2 Restore Sandbox Database: `dbrescue_test`
Used exclusively during disaster recovery tests, backup restore dry-runs, and parity checks:
- Contains an identical `test_records` table and a `restore_audit_metadata` table.
- Accessible only by `rescue_restore` user and database administrators.

---

## 4. Least-Privilege User Security Model

To prevent unauthorized privilege escalation and ensure separation of concerns:

| User Account | Permitted Hosts | Granted Privileges | Target Databases | Scope / Restrictions |
| :--- | :--- | :--- | :--- | :--- |
| **`rescue_app`** | `%` (Docker network) | `SELECT, INSERT, UPDATE, DELETE, CREATE TEMPORARY TABLES, LOCK TABLES` | `student_db.*` | Day-to-day web application traffic. Cannot alter schemas or access MySQL system tables. |
| **`rescue_backup`** | `%` (Docker network) | `SELECT, LOCK TABLES, SHOW VIEW, TRIGGER, EVENT`, `PROCESS` (global) | `student_db.*` | Logical backup generation (`mysqldump`). Read-only dump operator with lock inspection. |
| **`rescue_restore`**| `%` (Docker network) | `ALL PRIVILEGES` | `dbrescue_test.*` | Disaster recovery testing. Completely isolated from production `student_db`. |
| **`root`** | `localhost` / `127.0.0.1` | `ALL PRIVILEGES` | `*.*` | Emergency recovery and low-level administrative operations only. Never used by web apps. |

---

## 5. Operational Script Reference

All infrastructure scripts are located in `scripts/` and implement strict defensive guardrails:

* **`scripts/db-up.ps1` / `scripts/db-up.sh`**:
  - Validates Docker CLI and engine connectivity.
  - Launches `compose.yaml` in detached mode.
  - Monitors container health status with backoff and timeout.
* **`scripts/db-seed.ps1` / `scripts/db-seed.sh`**:
  - Idempotently applies `db/seed/seed_students.sql`.
  - Verifies and outputs counts for students (100), courses (10), and enrollments.
* **`scripts/db-status.ps1` / `scripts/db-status.sh`**:
  - Reports container runtime state, health, port bindings, and storage mounts.
  - Executes table row count checks against `student_db`.
