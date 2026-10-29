# dbrescue Operational Dashboard Screenshot Capture Guide

This guide details the exact steps and URLs to capture verified visual screenshots of every page in the dbrescue operational dashboard.

## Prerequisites
1. Start the Streamlit application:
   ```bash
   streamlit run app/main.py --server.port 8501 --server.address 127.0.0.1
   ```
2. Open your browser at `http://127.0.0.1:8501`.

---

## Required Page Screenshots & Verification Points

### 1. Overview Page (`docs/screenshots/01_overview.png`)
- **URL**: `http://127.0.0.1:8501/` (or select 'Overview' in sidebar)
- **What to capture**:
  - The 4 top KPI cards (MySQL Ping Latency, Docker State, Volume Status, RTO/RPO Scorecard).
  - The 18-stage workflow stepper visualizer.
  - The baseline vs. recovered record count chart.
  - Recent operational events table.

### 2. Live Health Page (`docs/screenshots/02_live_health.png`)
- **URL**: `http://127.0.0.1:8501/` -> Select 'Live Health'
- **What to capture**:
  - Latency gauge and active connection count.
  - Container CPU usage % and working set RSS memory.
  - The Named Volume Mount Inspector card (verifies `Type: volume` and flags any bind mounts).

### 3. Persistence Tests Page (`docs/screenshots/03_persistence_tests.png`)
- **URL**: `http://127.0.0.1:8501/` -> Select 'Persistence Tests'
- **What to capture**:
  - The destructive execution launch modal (requiring typed container confirmation token).
  - The live progress bar and stage transition logs.
  - Baseline vs. recovered count table and cryptographic SHA-256 fingerprint comparison.

### 4. Backup & Restore Page (`docs/screenshots/04_backup_restore.png`)
- **URL**: `http://127.0.0.1:8501/` -> Select 'Backup & Restore'
- **What to capture**:
  - Logical backup creation form with GZIP compression.
  - The Backup Archive Inventory table showing ID, SHA-256 hash, size, and status.
  - The Isolated Dry-Run Restore panel with confirmation token `RESTORE-<id>`.
  - Retention policy preview panel.

### 5. Recovery Timeline & Audit Logs (`docs/screenshots/05_timeline_audit.png`)
- **URL**: `http://127.0.0.1:8501/` -> Select 'Recovery Timeline' or 'Audit Logs'
- **What to capture**:
  - Chronological stage progression timeline.
  - The forensic log explorer with Run ID, operation, and severity filters.

### 6. Alerts Console (`docs/screenshots/06_alerts.png`)
- **URL**: `http://127.0.0.1:8501/` -> Select 'Alerts'
- **What to capture**:
  - Active incidents callout (if any rules are FIRING).
  - The 7 standard operational rules evaluation matrix showing `NOT_EVALUATED` vs. affirmative evidence.

### 7. Configuration & Certified Reports (`docs/screenshots/07_configuration_reports.png`)
- **URL**: `http://127.0.0.1:8501/` -> Select 'Configuration & Reports'
- **What to capture**:
  - Operational boundary controls (dataset size, sandbox timeout, CPU threshold).
  - CSV / JSON compliance export buttons.
  - Signed PDF recovery certificate download button with compiled report preview.
