# Contributing to dbrescue

Thank you for your interest in contributing to **dbrescue**! We welcome contributions that improve disaster recovery reliability, cryptographic integrity validation, observability, and infrastructure security.

---

## 1. Core Principles & Safety Invariants

Before writing any code or modifying documentation, please review our strict invariants in [SECURITY.md](SECURITY.md) and [docs/CONTRACTS.md](docs/CONTRACTS.md):

1. **Persistent Volumes Must Be Preserved**: Never use `down -v`, `volume rm`, or prune persistent Docker storage.
2. **Parameterized SQL Only**: Dynamic SQL concatenation for query values or filters is strictly forbidden and blocked by static CI analysis.
3. **No Synthetic / Fake Data**: Operational metrics, status cards, and audit events must reflect affirmative evidence or render neutral empty states (`NOT_EVALUATED` / `UNKNOWN`).
4. **Credential Hygiene**: Never hardcode credentials or log sensitive parameters.

---

## 2. Local Development Setup

### Prerequisites
- Python 3.11, 3.12, or 3.13
- Git
- Docker Desktop with Compose v2 (recommended for live integration tests)

### Quick Environment Setup
```bash
# Clone the repository
git clone https://github.com/Narendrapendli/dbrescue.git
cd dbrescue

# Using Makefile (Linux / macOS)
make setup

# Or using PowerShell (Windows)
.\tasks.ps1 setup
```

---

## 3. Development Workflow & Standards

### Linting & Formatting
Code formatting and linting are strictly enforced using **Ruff**:
```bash
# Check code style & linting rules
ruff check .

# Automatically apply formatting
ruff format .

# Verify formatting compliance
ruff format --check .
```

### Running the Test Suite
All contributions must pass the automated pytest test suite:
```bash
# Run unit, security, error, and backup test suites
pytest -m "not integration" -v

# Run full acceptance report generator
python scripts/make_test_report.py
```

---

## 4. Submitting a Pull Request (PR)

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. Commit your changes using conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
3. Ensure all acceptance tests pass and coverage does not regress.
4. Open a Pull Request against `main`. Fill out all sections of the [Pull Request Template](.github/pull_request_template.md), confirming compliance with the security invariant checklist.
