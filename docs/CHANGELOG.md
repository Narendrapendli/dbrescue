# dbrescue Platform Engineering Changelog

All notable changes across subsystems, architectural interfaces, and bug resolutions are documented in this file.

---

## [1.0.0] - 2026-10-29 - Integration & Release Milestone (Prompt 13)

### Resolved Interface Mismatches & Fixes
- **`src/dbrescue/restore.py` (`RestoreEngine.__init__`)**:
  - *Issue*: `RestoreEngine.__init__` typed `sqlite_db_path: str` and stored it as `str(sqlite_db_path)`. When no `integrity_engine` was injected, it passed the `str` into `IntegrityEngine(self.sqlite_db_path)`, which failed when calling `path.parent.mkdir()`.
  - *Fix*: Updated parameter to `Path | str` and resolved to `Path(sqlite_db_path).resolve()`.
- **`src/dbrescue/docker_manager.py` (`DockerManager.is_docker_available`)**:
  - *Issue*: Function accepted only `timeout_sec` parameter name; callers supplying `timeout` as a keyword argument raised `TypeError`.
  - *Fix*: Added backward-compatible signature `is_docker_available(self, timeout_sec: float = 3.0, timeout: float | None = None)` evaluating `eff_timeout = timeout if timeout is not None else timeout_sec`.
- **`.gitignore` & SQL Migration Whitelist**:
  - *Issue*: Root `.gitignore` previously had `*.sql` which ignored `db/init/` and `db/seed/` SQL definitions.
  - *Fix*: Added whitelisting rules `!db/init/*.sql` and `!db/seed/*.sql`.
- **Formatting Standardization**:
  - Formatted all 72 Python files across `src/`, `app/`, `tests/`, and `scripts/` using `ruff format` to guarantee 100% compliance with `ruff format --check .`.

### Features & Additions
- **Monitoring Infrastructure (Prompt 12)**:
  - Added `compose.monitoring.yaml` compose overlay with profile `monitoring`.
  - Added `db/init/03_exporter_user.sql` for least-privilege mysqld-exporter account (`PROCESS`, `REPLICATION CLIENT`, `SELECT ON performance_schema.*`).
  - Added `monitoring/prometheus.yml` (scrapes MySQL, cAdvisor, and DBRescue metrics) and `monitoring/alert_rules.yml` (7 alert rules).
  - Added Grafana automated provisioning and `monitoring/grafana/dashboards/dbrescue_overview.json` (13 panels).
  - Added comprehensive `docs/MONITORING.md` runbook.
- **CI/CD & Repository Automation (Prompt 11)**:
  - Added `.github/workflows/ci.yml` (lint, unit tests matrix across Python 3.11, 3.12, 3.13, security, integration tests, and test-report upload).
  - Added `.github/dependabot.yml` and `.github/pull_request_template.md`.
  - Added `Makefile` and `tasks.ps1` cross-platform task runners.
  - Added `README.md` with dynamic status badges.
- **Automated Test Suite & Verification (Prompt 10)**:
  - 136 total tests collected; 133 passed, 3 environment-gated skipped, 0 failures.
  - Added negative error testing suite (`tests/errors/test_error_handling.py`).
  - Added static security audit suite (`tests/security/test_security_audit.py`).
  - Added `scripts/make_test_report.py` and `docs/TEST_REPORT.md`.
