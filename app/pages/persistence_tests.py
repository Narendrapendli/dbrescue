"""Persistence Tests Page for dbrescue Dashboard.

Executes the end-to-end 18-stage disaster recovery test with mandatory safety confirmation,
live stage stepper, row-level parity verification, diff detection, and historical drill-down.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components import (
    render_empty_state,
    render_workflow_stepper,
)
from app.services import (
    execute_persistence_test,
    generate_recovery_report,
    get_config,
    get_persistence_runs,
    get_run_snapshots,
)


def render_persistence_tests_page() -> None:
    """Render the Persistence Testing and Disaster Recovery Runner view."""
    st.title("Disaster Recovery & Volume Persistence Test Runner")
    st.caption("Perform rigorous container crash simulation and prove zero-data-loss volume persistence.")

    cfg = get_config()
    target_container = cfg.docker.primary_container_name
    target_volume = cfg.docker.target_volume_name

    # Action Banner & Safety Guard Dialog
    st.markdown("### Execute Persistence Test")
    st.markdown(
        f"""
        This test will inject run-scoped synthetic student records, compute a cryptographic SHA-256
        fingerprint of the database, **forcibly stop and remove** the target container (`{target_container}`),
        recreate a fresh container attached to the exact same named volume (`{target_volume}`),
        and verify 100% row-for-row parity.
        """
    )

    with st.expander("⚠️ Run Disaster Recovery Simulation", expanded=True):
        st.warning(
            f"DESTRUCTIVE CONTAINER OPERATION: The container **`{target_container}`** will be removed. "
            "Data volume will be preserved intact."
        )

        test_records = st.number_input(
            "Test Record Batch Size", min_value=10, max_value=500, value=50, step=10
        )
        typed_confirmation = st.text_input(
            f"Type target container name to confirm ({target_container}):",
            key="confirm_container_input",
            placeholder=target_container,
        )

        btn_run = st.button("🚀 Run Disaster Recovery Test", type="primary")

        if btn_run:
            if typed_confirmation.strip() != target_container:
                st.error(
                    f"Safety check failed: You must type '{target_container}' exactly to proceed."
                )
            else:
                st.info("Beginning 18-stage disaster recovery lifecycle...")
                progress_bar = st.progress(0)
                status_placeholder = st.empty()

                def on_progress(stage_code: str, percent: int) -> None:
                    progress_bar.progress(percent)
                    status_placeholder.markdown(f"**Current Stage:** `{stage_code}` ({percent}%)")

                try:
                    with st.spinner("Executing disaster recovery state machine..."):
                        result = execute_persistence_test(
                            confirm_container_name=typed_confirmation,
                            record_count=int(test_records),
                            progress_callback=on_progress,
                        )

                    progress_bar.progress(100)
                    if result.passed:
                        st.success(
                            f"🎉 Disaster Recovery Verified! Final Stage: {result.final_stage.value}. "
                            f"Duration: {result.duration_seconds:.2f}s (RTO)."
                        )
                    else:
                        st.error(
                            f"❌ Recovery Test Failed at stage `{result.final_stage.value}`. Reason: {result.error_message}"
                        )
                except Exception as exc:
                    st.error(f"Execution Error: {exc}")

    st.divider()

    # 18-Stage Stepper Visualization
    render_workflow_stepper()

    st.divider()

    # Historical Runs & Parity Diff Viewer
    st.markdown("### Historical Recovery Runs")
    runs = get_persistence_runs(limit=15)

    if not runs:
        render_empty_state(
            title="No Disaster Recovery Runs Recorded",
            message="No historical disaster recovery test runs exist in SQLite. Execute a test run above to populate historical audits.",
        )
        return

    # Table of Runs
    run_rows = []
    for r in runs:
        run_rows.append({
            "Run ID": r.get("id"),
            "UUID": r.get("run_uuid")[:8] + "...",
            "Status": r.get("status"),
            "Records": r.get("records_tested"),
            "RTO (s)": round(r.get("duration_seconds") or 0.0, 2),
            "Parity Verified": "YES" if r.get("parity_verified") else "NO",
            "Timestamp (UTC)": r.get("ended_at") or r.get("created_at"),
        })

    df_runs = pd.DataFrame(run_rows)
    st.dataframe(df_runs, use_container_width=True)

    # Drill-down into specific run
    run_ids = [r["id"] for r in runs]
    selected_run_id = st.selectbox("Inspect Parity Details for Run ID", options=run_ids)

    if selected_run_id:
        snapshots = get_run_snapshots(selected_run_id)
        base = snapshots.get("baseline")
        rec = snapshots.get("recovered")

        st.markdown(f"#### Parity Verification Breakdown — Run #{selected_run_id}")
        col_b, col_r, col_p = st.columns(3)

        with col_b:
            st.metric(
                "Baseline Snapshot",
                f"{base.get('row_count') if base else 'UNKNOWN'} rows",
                f"SHA-256: {base.get('fingerprint')[:16]}..." if base else "",
            )
        with col_r:
            st.metric(
                "Recovered Snapshot",
                f"{rec.get('row_count') if rec else 'UNKNOWN'} rows",
                f"SHA-256: {rec.get('fingerprint')[:16]}..." if rec else "",
            )
        with col_p:
            if base and rec:
                is_match = (
                    base.get("row_count") == rec.get("row_count")
                    and base.get("fingerprint") == rec.get("fingerprint")
                )
                st.metric("Parity Check", "PASSED" if is_match else "FAILED")
            else:
                st.metric("Parity Check", "INCOMPLETE")

        # Download PDF Certificate button
        if st.button(f"📄 Download Signed Recovery Certificate (Run #{selected_run_id})"):
            try:
                pdf_path = generate_recovery_report(selected_run_id)
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
                st.download_button(
                    label="💾 Click to Download PDF",
                    data=pdf_bytes,
                    file_name=f"dbrescue_certificate_run_{selected_run_id}.pdf",
                    mime="application/pdf",
                )
            except Exception as exc:
                st.error(f"Report generation error: {exc}")
