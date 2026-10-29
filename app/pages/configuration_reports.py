"""Configuration & Forensic Reports Page for dbrescue Dashboard.

Dynamic SQLite operational settings management, forensic data exports (JSON/CSV),
and ReportLab PDF recovery certificate compiler from real run records.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.components import render_empty_state
from app.services import (
    generate_recovery_report,
    get_all_settings,
    get_backups,
    get_persistence_runs,
    get_recent_audit_events,
    update_setting,
)


def render_configuration_reports_page() -> None:
    """Render the configuration settings and audit reporting interface."""
    st.title("Configuration & Forensic Audit Reports")
    st.caption(
        "Manage runtime operational boundaries, export audit trails, and compile signed PDF certificates."
    )

    tab_cfg, tab_export, tab_pdf = st.tabs(
        ["⚙️ Operational Settings", "📤 Data Export (JSON/CSV)", "📄 Signed PDF Certificates"]
    )

    # --- TAB 1: OPERATIONAL SETTINGS ---
    with tab_cfg:
        st.markdown("### Runtime Operational Parameters")
        st.caption(
            "Settings are stored in the SQLite operational metadata store and take effect immediately."
        )

        settings = get_all_settings()

        col1, col2 = st.columns(2)
        with col1:
            dataset_limit = st.number_input(
                "Max Dataset Size (MB)",
                min_value=100,
                max_value=100000,
                value=int(settings.get("max_dataset_size_mb", 10240)),
            )
            sandbox_timeout = st.number_input(
                "Sandbox Readiness Timeout (Seconds)",
                min_value=10,
                max_value=300,
                value=int(settings.get("sandbox_timeout_sec", 60)),
            )
        with col2:
            cpu_thresh = st.number_input(
                "CPU Usage Alert Threshold (%)",
                min_value=10.0,
                max_value=99.0,
                value=float(settings.get("cpu_threshold_pct", 85.0)),
                step=5.0,
            )
            retention_count = st.number_input(
                "Retain Last Verified Backups (Count)",
                min_value=1,
                max_value=50,
                value=int(settings.get("backup_retention_count", 5)),
            )

        if st.button("💾 Save Operational Settings", type="primary"):
            try:
                update_setting("max_dataset_size_mb", dataset_limit)
                update_setting("sandbox_timeout_sec", sandbox_timeout)
                update_setting("cpu_threshold_pct", cpu_thresh)
                update_setting("backup_retention_count", retention_count)
                st.success("✓ Operational boundaries saved successfully.")
            except Exception as exc:
                st.error(f"Failed to update settings: {exc}")

    # --- TAB 2: DATA EXPORT ---
    with tab_export:
        st.markdown("### Forensic Evidence Export")
        st.caption(
            "Export structured operational records for compliance auditing and external analytics."
        )

        export_choice = st.selectbox(
            "Select Dataset to Export",
            ["Historical Persistence Runs", "Backup Archive Metadata", "Recent Audit Trail Events"],
        )

        if export_choice == "Historical Persistence Runs":
            data = get_persistence_runs(limit=200)
        elif export_choice == "Backup Archive Metadata":
            data = get_backups(limit=200)
        else:
            data = get_recent_audit_events(limit=500)

        if not data:
            st.info("No records available to export for this dataset.")
        else:
            df = pd.DataFrame(data)
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            json_bytes = json.dumps(data, indent=2, default=str).encode("utf-8")

            col_csv, col_json = st.columns(2)
            with col_csv:
                st.download_button(
                    label=f"⬇️ Download {export_choice} (CSV)",
                    data=csv_bytes,
                    file_name=f"dbrescue_{export_choice.lower().replace(' ', '_')}.csv",
                    mime="text/csv",
                )
            with col_json:
                st.download_button(
                    label=f"⬇️ Download {export_choice} (JSON)",
                    data=json_bytes,
                    file_name=f"dbrescue_{export_choice.lower().replace(' ', '_')}.json",
                    mime="application/json",
                )

    # --- TAB 3: SIGNED PDF CERTIFICATES ---
    with tab_pdf:
        st.markdown("### Certified Disaster Recovery PDF Reports")
        st.caption(
            "Generates publication-grade PDF recovery certificates containing run UUID, timestamps, "
            "baseline vs recovered row counts, SHA-256 fingerprints, and non-repudiable audit trails."
        )

        runs = get_persistence_runs(limit=50)
        if not runs:
            render_empty_state(
                title="No Runs Available for Certification",
                message="PDF certificates can only be generated from real disaster recovery runs. Execute a persistence test first.",
            )
        else:
            run_options = {
                f"Run #{r['id']} ({r.get('run_uuid')[:8]}... - {r.get('status')})": r["id"]
                for r in runs
            }
            sel_label = st.selectbox(
                "Select Disaster Recovery Run", options=list(run_options.keys())
            )
            chosen_run_id = run_options[sel_label]

            if st.button("📄 Compile Certified PDF Report", type="primary"):
                with st.spinner("Compiling vector PDF document and JSON metadata..."):
                    try:
                        pdf_path = generate_recovery_report(chosen_run_id)
                        with open(pdf_path, "rb") as f:
                            pdf_data = f.read()

                        st.success(
                            f"✓ PDF report successfully compiled: `{pdf_path.name}` ({len(pdf_data):,} bytes)"
                        )
                        st.download_button(
                            label="⬇️ Click Here to Download PDF Report",
                            data=pdf_data,
                            file_name=f"dbrescue_run_{chosen_run_id}_certificate.pdf",
                            mime="application/pdf",
                        )
                    except Exception as exc:
                        st.error(f"PDF compilation failed: {exc}")
