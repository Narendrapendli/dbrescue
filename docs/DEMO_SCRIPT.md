# dbrescue 5-Minute Executive Demo & Presentation Script

> **Scenario:** Demonstrating Task #38 — Proving zero data loss across container destruction and recreation when MySQL 8.4 LTS runs on a Docker persistent volume (`/var/lib/mysql`).

---

## Presentation Timeline Overview

| Timestamp | Phase | Topic / Action | Command / URL |
| :---: | :--- | :--- | :--- |
| **0:00 - 0:30** | 1. Introduction | Platform objective, architecture, and safety invariants | Slides / Architecture Diagram |
| **0:30 - 1:15** | 2. Stack Startup | Boot MySQL 8.4 LTS container & seed initial database fixtures | `docker compose up -d` & `.\tasks.ps1 seed` |
| **1:15 - 2:00** | 3. Dashboard Tour | Dark navy UI, live health metrics, volume mount inspector | `http://127.0.0.1:8501` |
| **2:00 - 3:30** | 4. Persistence Test | 18-stage disaster recovery state machine: delete container, keep volume, recreate, compare fingerprints | Dashboard -> 'Persistence Tests' |
| **3:30 - 4:15** | 5. Backup & Restore | GZIP backup streaming, SHA-256 verify, isolated sandbox restore | Dashboard -> 'Backup & Restore' |
| **4:15 - 4:45** | 6. Negative Drills | Tampered row detection & safety confirmation token guard | `python scripts/run_e2e_drills.py` |
| **4:45 - 5:00** | 7. Executive Report | Certified ReportLab vector PDF certificate download & wrap-up | Dashboard -> 'Configuration & Reports' |

---

## Detailed Step-by-Step Walkthrough

### Step 1: Introduction (0:00 - 0:30)
*Presenter Talking Points:*
> *"Welcome! Today we are demonstrating **dbrescue** — an automated disaster recovery, integrity auditing, and observability platform for MySQL 8.4 LTS in Docker. In traditional container environments, killing a database container often causes panic about data persistence. Today we will prove that data in a Docker named volume mounted to `/var/lib/mysql` survives complete container destruction, and we will verify 100% data integrity down to the exact row hash."*

---

### Step 2: Stack Startup & Database Seeding (0:30 - 1:15)
1. **Launch the core infrastructure**:
   ```bash
   # Windows PowerShell
   .\tasks.ps1 up

   # Or Linux / macOS
   make up
   ```
2. **Seed realistic student and course fixtures**:
   ```bash
   # Windows PowerShell
   .\tasks.ps1 seed

   # Or Linux / macOS
   make seed
   ```
3. *Show the terminal output proving seed records exist*:
   ```text
   +-------------+-------+
   | tbl         | count |
   +-------------+-------+
   | students    |   100 |
   | courses     |    15 |
   | enrollments |   250 |
   +-------------+-------+
   ```

---

### Step 3: Launch Operational Dashboard (1:15 - 2:00)
1. **Start the Streamlit control plane**:
   ```bash
   streamlit run app/main.py --server.port 8501 --server.address 127.0.0.1
   ```
2. Open browser at `http://127.0.0.1:8501`.
3. *Point out key features*:
   - Dark navy theme (`#0A0F1D`).
   - Real MySQL latency query (`SELECT 1;`) and active connections.
   - **Named Volume Inspector**: Proves mount is `Type: volume` targeting `dbrescue_mysql_data` with zero illegal bind mounts.

---

### Step 4: Execute the Disaster Recovery Persistence Test (2:00 - 3:30)
*This is the core proof of Task #38.*

1. Navigate to the **Persistence Tests** page.
2. Click **Run Disaster Recovery Test**.
3. *Show the Safety Confirmation Modal*:
   - Explain: *"To prevent accidental execution, the user must type the exact container name (`dbrescue-mysql-primary`)."*
   - Type `dbrescue-mysql-primary` and click **Confirm & Execute**.
4. *Watch the 18-stage Workflow Stepper in real time*:
   - Stage 1-4: Discovery and pre-flight validation.
   - Stage 5-8: Read-only storage forensics; captures baseline row count (365 rows) and SHA-256 table fingerprint.
   - Stage 9-11: **The Container is Completely Stopped and Destroyed**. Named volume `dbrescue_mysql_data` is preserved.
   - Stage 12-14: New container is recreated from scratch using the exact same volume.
   - Stage 15-18: Post-recovery query captures recovered rows and compares against the baseline snapshot.
5. *Show the Final Result*:
   - Status: <span style="color: #10B981; font-weight: bold;">PASSED</span>.
   - Baseline Rows: `365` | Recovered Rows: `365` | Discrepancy: `0`.
   - Fingerprint Equality: `100% Cryptographic Match`.

---

### Step 5: Logical Backup & Isolated Parity Restore (3:30 - 4:15)
1. Navigate to the **Backup & Restore** page.
2. Click **Trigger Backup**:
   - Streams `mysqldump` directly through GZIP with `--single-transaction`.
   - Generates archive with inline SHA-256 checksum.
3. Click **Recompute & Verify Checksum**:
   - Reads archive from disk; re-computes hash to prove zero bit-rot.
4. Click **Run Isolated Restore Test**:
   - Type confirmation token `RESTORE-<id>`.
   - Restores the archive into an ephemeral database (`dbrescue_restore_<id>`).
   - Verifies 100% row and fingerprint parity, then drops the isolated database safely.

---

### Step 6: Negative Failure Drills Demonstration (4:15 - 4:45)
*Presenter Talking Points:*
> *"A disaster recovery tool is only trustworthy if it correctly fails when corruption occurs. Let's run our negative drill suite."*

1. Run the negative drill engine:
   ```bash
   python scripts/run_e2e_drills.py
   ```
2. *Highlight the real drill outcomes*:
   - **Row Tampering Drill**: Status immediately transitions to `FAILED`/`DEGRADED`; divergence trapped.
   - **Docker Down Drill**: Halts safely at Stage 2 with `DockerUnavailable`.
   - **Missing Volume Drill**: Halts safely at Stage 3 with `VolumeMissing`.
   - **Readiness Timeout Drill**: Halts safely at Stage 12 with `ReadinessTimeout`.

---

### Step 7: Certified PDF Report & Conclusion (4:45 - 5:00)
1. Navigate to **Configuration & Reports** -> **Signed PDF Certificates**.
2. Select the completed recovery run and click **Compile Certified PDF Report**.
3. Download and open the PDF report:
   - Point out the formal header, run UUID, timestamp, baseline vs. recovered table comparison, and digital sign-off.
4. *Concluding Remark*:
   > *"In 5 minutes, we have proven zero data loss across container removal, validated end-to-end cryptographic integrity, verified backups in isolated sandboxes, and generated a signed compliance certificate. Thank you!"*
