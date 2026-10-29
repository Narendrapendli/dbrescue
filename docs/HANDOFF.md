# dbrescue Operations, Engineering & Handoff Reference

## 1. Project Posture & Test Results
- **Overall Test Suite Status:** **133 Passed, 3 Skipped (0 Failures)** out of 136 tests.
- **Backend Code Coverage:** **80.0%** across `src/dbrescue`.
- **Linter & Code Quality:** 100% compliant with Ruff rules (`ruff check` passes clean).
- **Security Audit:** 17/17 security tests passed (no hardcoded credentials, loopback port binding strictly enforced, log redaction active, allowlists verified, no `down -v` anywhere in repository).

---

## 2. Identified Subsystem Observations & Edge Cases

### Finding 1: `RestoreEngine` SQLite Path Type Handling
- **Observation:** `RestoreEngine.__init__` accepts `sqlite_db_path: str` and converts it via `str(sqlite_db_path)`. When `integrity_engine` is not explicitly injected, it initializes `IntegrityEngine(self.sqlite_db_path)` passing a `str`. `IntegrityEngine` expects a `pathlib.Path` instance to call `.parent.mkdir()`.
- **Recommended Resolution for Future Sprint:** Update `RestoreEngine.__init__` parameter type to `Path | str` and wrap as `self.sqlite_db_path = Path(sqlite_db_path).resolve()`.
- **Reproducible Test:**
  ```python
  from dbrescue.restore import RestoreEngine
  # Raises AttributeError: 'str' object has no attribute 'parent'
  engine = RestoreEngine(sqlite_db_path="./db/sqlite/operational.db")
  ```

### Finding 2: `DockerManager.is_docker_available` Signature
- **Observation:** `DockerManager.is_docker_available()` currently takes no timeout parameter and relies on client ping defaults. Callers passing `timeout` keyword argument raise `TypeError`.
- **Recommended Resolution for Future Sprint:** Add optional `timeout: float = 3.0` parameter to `is_docker_available`.

---

## 3. Operational Quick Reference
- **Run Acceptance Test Suite:**
  - PowerShell: `.\scripts\run-tests.ps1`
  - Bash: `./scripts/run-tests.sh`
- **Recompile Test Report & Coverage Matrix:**
  - `python scripts/make_test_report.py` (updates `docs/TEST_REPORT.md` and `reports/junit.xml`)
- **Launch Operational Streamlit Console:**
  - `streamlit run app/main.py`
