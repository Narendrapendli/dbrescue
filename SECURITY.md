# Security Policy & Invariant Architecture (SECURITY.md)

The `dbrescue` platform is designed for automated disaster recovery, integrity auditing, and observability across Docker MySQL 8.4 LTS production databases. Because recovery software has access to database storage and container engines, it enforces defense-in-depth architectural invariants.

---

## 1. Supported Versions

Security updates and vulnerability patches are actively provided for:

| Version | Supported | Status |
| :--- | :---: | :--- |
| `1.0.x` | Yes | Active Long-Term Support (LTS) |
| `< 1.0.0` | No | Pre-release / Prototype |

---

## 2. Core Architectural Security Invariants

All code, configurations, and scripts in this repository strictly adhere to six immutable security rules:

### Invariant 1: Persistent Storage Volume Preservation
- **Rule**: `docker compose down -v`, `docker volume rm`, or volume pruning commands are **strictly prohibited** in all code, scripts, test fixtures, and documentation.
- **Enforcement**: Automated static grep guards in CI (`tests/security/test_security_audit.py`) and runtime safety interceptors in `tests/conftest.py` immediately halt execution if volume deletion is attempted.

### Invariant 2: Strict Loopback Network Isolation
- **Rule**: MySQL, Prometheus, Grafana, and exporter host ports must bind **exclusively to `127.0.0.1`** (never `0.0.0.0` or wildcard interfaces).
- **Enforcement**: Automated static checking of `compose.yaml` and `compose.monitoring.yaml` guarantees no public listener exposure. Production deployments must place services behind authenticated reverse proxies or VPNs.

### Invariant 3: Strictly Parameterized SQL (Zero Dynamic Concatenation)
- **Rule**: Every database query must use parameterized placeholders (`%s` in MySQL, `?` in SQLite). String interpolation (`f"..."`, `+`, `.format()`) for data values, filters, or `WHERE`/`VALUES`/`SET` clauses is forbidden.
- **Enforcement**: AST static analysis (`test_parameterized_sql_static_scan`) traverses the codebase during CI to detect any dynamic query assembly. Validated identifiers are checked against `^[a-zA-Z0-9_]+$`.

### Invariant 4: Credential Hygiene & Log Redaction
- **Rule**: Passwords, tokens, and cryptographic keys are never hardcoded, never output to the console, and never serialized to log files or audit tables.
- **Enforcement**:
  - `SecretStr` wrappers mask passwords in `__repr__`.
  - Logging filters intercept and redact database connection strings and regex patterns (`password=***`).
  - `.env` and `backups/` are strictly ignored by `.gitignore` and verified by automated tests.

### Invariant 5: Least-Privilege Database Role Separation
- **Rule**: Root database accounts are forbidden for day-to-day operations, application queries, backups, and restores.
- **Roles**:
  - `rescue_app`: Limited to DML operations (`SELECT`, `INSERT`, `UPDATE`, `DELETE`) on `student_db`.
  - `rescue_backup`: Read-only logical dumps (`SELECT`, `LOCK TABLES`, `SHOW VIEW`, `PROCESS`).
  - `rescue_restore`: Isolated strictly to temporary `dbrescue_restore_*` databases; cannot alter primary schemas.
  - `exporter`: Monitoring-only privileges (`PROCESS`, `REPLICATION CLIENT`, `SELECT ON performance_schema.*`).

### Invariant 6: Docker Socket Containment & Allowlisting
- **Rule**: Container execution via the Docker SDK is restricted to allowlisted commands (`mysqldump`, `mysql`, `gzip`, `cat`, `sha256sum`, `stat`). Arbitrary shell command execution inside containers is blocked.

---

## 3. Reporting a Vulnerability

If you discover a security vulnerability or invariant violation within `dbrescue`:

1. **Do NOT open a public GitHub issue.**
2. Email full technical details, reproduction steps, and diagnostic logs to the security team:
   - **Email:** `security@dbrescue.local` (or reach out via private security advisory on GitHub).
3. The team will acknowledge receipt within **24 hours** and provide a patch/mitigation timeline within **72 hours**.
4. We request that you observe responsible disclosure principles until an official security release is published.
