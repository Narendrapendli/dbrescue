# ==============================================================================
# dbrescue Automation Makefile
# Disaster Recovery, Integrity Auditing & Observability Platform
# SAFETY GUARD: Persistent storage volumes are strictly preserved.
# ==============================================================================

.PHONY: help setup up seed test lint run-app test-persistence backup down

PYTHON ?= python
PIP ?= pip
COMPOSE ?= docker compose

help:
	@echo "dbrescue Management Commands:"
	@echo "  make setup            - Install development and production dependencies"
	@echo "  make up               - Start MySQL infrastructure container (preserves volume)"
	@echo "  make seed             - Seed database with student, course, and enrollment fixtures"
	@echo "  make test             - Run complete automated pytest test suite"
	@echo "  make lint             - Run ruff linter and formatting checks"
	@echo "  make run-app          - Launch the Streamlit operational dashboard"
	@echo "  make test-persistence - Execute 18-stage disaster recovery state machine test"
	@echo "  make backup           - Generate logical mysqldump compressed backup"
	@echo "  make down             - Safely stop compose containers (NEVER with -v)"

setup:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

up:
	$(COMPOSE) up -d mysql

seed:
	@bash scripts/db-seed.sh

test:
	$(PYTHON) -m pytest -v

lint:
	ruff check .
	ruff format --check .

run-app:
	streamlit run app/main.py --server.port 8501 --server.address 127.0.0.1

test-persistence:
	$(PYTHON) -c "from dbrescue.recovery import RecoveryOrchestrator; from dbrescue.config import load_config; res = RecoveryOrchestrator(load_config()).run(confirm=True); print('Recovery Result:', res.status)"

backup:
	$(PYTHON) -c "from dbrescue.backup import BackupEngine; from dbrescue.config import load_config; meta = BackupEngine(load_config()).create_logical_backup(label='makefile-backup'); print('Created backup:', meta.filename, meta.sha256_checksum)"

down:
	@echo "Safely stopping containers without removing named volume..."
	$(COMPOSE) down
