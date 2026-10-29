## Summary of Changes

<!-- Provide a concise description of the changes introduced by this pull request. -->

## Type of Change

- [ ] `feat`: New feature or capability
- [ ] `fix`: Bug fix
- [ ] `refactor`: Code refactoring without behavioral change
- [ ] `test`: Test suite expansion or hardening
- [ ] `docs`: Documentation update
- [ ] `chore`: CI/CD pipeline, dependency, or configuration update

## Architecture & Security Verification Checklist

Before submitting, confirm that all security and operational invariants from `docs/CONTRACTS.md` are satisfied:

- [ ] **Zero Destructive Volume Deletions**: Confirmed that `down -v`, `volume rm`, or `docker volume prune` are **NEVER** executed or added.
- [ ] **Parameterized SQL**: Confirmed that all SQL queries use parameterized bindings (no dynamic f-string interpolation for values or filter criteria).
- [ ] **Loopback Network Isolation**: Confirmed that all host port bindings in Docker/compose strictly bind to `127.0.0.1` (never `0.0.0.0` or wildcard).
- [ ] **Credential Hygiene**: Verified no secrets, passwords, or tokens are committed; `.env` is ignored by git.
- [ ] **Evidence & Telemetry Integrity**: Verified that no fake, simulated, or demo metrics are displayed; missing telemetry renders neutral empty states.
- [ ] **Code Formatting & Linting**: Verified that `ruff check .` and `ruff format --check .` pass with zero errors.
- [ ] **Test Suite Parity**: Verified that all unit and offline tests pass locally via `pytest -m "not integration"` without regressions.

## Verification Evidence

<!-- Provide the output or summary of local test runs (e.g. pytest summary or TEST_REPORT.md snippet). -->
```text
(paste local test run output here)
```
