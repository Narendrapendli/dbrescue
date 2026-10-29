"""Overview Page for dbrescue Dashboard.

Visualizes current infrastructure health, latest persistence test run,
backup verification, alerts, and operational reliability scorecard.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components import (
    render_empty_state,
    render_kpi_card,
    render_reliability_scorecard,
    render_volume_inspector_card,
)
from app.services import (
    get_active_alerts,
    get_container_health,
    get_database_health,
    get_latest_backup,
    get_latest_persistence_run,
    get_recent_audit_events,
    get_reliability_scorecard,
    get_run_snapshots,
    get_volume_health,
)
from app.theme import COLOR_FAILED, COLOR_SUCCESS, COLOR_UNKNOWN


def render_overview_page() -> None:
    """Render the main operations overview dashboard."""
    st.title("System Overview & Disaster Recovery Status")
    st.caption("Real-time telemetry and volume persistence audit posture.")

    # 1. System Health KPIs Ribbon
    db_health = get_database_health()
    cntr_health = get_container_health()
    vol_health = get_volume_health()
    latest_run = get_latest_persistence_run()
    latest_backup = get_latest_backup()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        db_status = "ONLINE" if db_health.get("reachable") else "OFFLINE"
        db_color = COLOR_SUCCESS if db_health.get("reachable") else COLOR_FAILED
        lat = db_health.get("latency_ms")
        lat_text = f"Latency: {lat:.1f}ms" if lat is not None else "Reachable"
        render_kpi_card(
            title="MySQL Database",
            value=db_status,
            subtitle=f"{lat_text} • Uptime {db_health.get('uptime_seconds', 0)}s",
            accent_color=db_color,
        )

    with col2:
        c_state = cntr_health.get("state", "UNKNOWN").upper()
        c_color = COLOR_SUCCESS if c_state == "RUNNING" else COLOR_FAILED
        c_sub = f"Health: {cntr_health.get('health_status', 'UNKNOWN')}"
        render_kpi_card(
            title="MySQL Container",
            value=c_state,
            subtitle=c_sub,
            accent_color=c_color,
        )

    with col3:
        v_ok = vol_health.get("mounted_correctly", False)
        v_status = "MOUNTED" if v_ok else "MIS-MOUNTED"
        v_color = COLOR_SUCCESS if v_ok else COLOR_FAILED
        render_kpi_card(
            title="Named Volume",
            value=v_status,
            subtitle=f"{vol_health.get('volume_name', 'mysql_data')} • {vol_health.get('size_human', 'UNKNOWN')}",
            accent_color=v_color,
        )

    with col4:
        if latest_run:
            run_status = latest_run.get("status", "UNKNOWN").upper()
            run_color = COLOR_SUCCESS if run_status == "SUCCESS" else COLOR_FAILED
            render_kpi_card(
                title="Last DR Test",
                value=run_status,
                subtitle=f"Run #{latest_run.get('id')} • {latest_run.get('duration_seconds', 0):.1f}s RTO",
                accent_color=run_color,
            )
        else:
            render_kpi_card(
                title="Last DR Test",
                value="NOT TESTED",
                subtitle="No persistence runs recorded",
                accent_color=COLOR_UNKNOWN,
            )

    st.divider()

    # 2. Reliability Scorecard
    scorecard = get_reliability_scorecard()
    render_reliability_scorecard(scorecard)

    st.divider()

    # 3. Persistence Test Parity Breakdown & Volume Inspector
    left_col, right_col = st.columns([1.2, 1])

    with left_col:
        st.markdown("### Latest Volume Persistence Test")
        if not latest_run:
            render_empty_state(
                title="No Persistence Tests Executed",
                message="No disaster recovery runs have been recorded in the audit store. Run a simulated container crash and recreate on the Persistence Tests page to verify volume survival.",
                action_label="Go to Persistence Tests →",
            )
        else:
            st.markdown(
                f"**Run ID:** `{latest_run.get('run_uuid')}`  \n"
                f"**Completed At:** {latest_run.get('ended_at')} (Duration: {latest_run.get('duration_seconds', 0):.2f}s)"
            )
            snapshots = get_run_snapshots(latest_run["id"])
            baseline = snapshots.get("baseline")
            recovered = snapshots.get("recovered")

            if baseline and recovered:
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.metric("Baseline Count", baseline.get("row_count", 0))
                with c2:
                    st.metric("Recovered Count", recovered.get("row_count", 0))
                with c3:
                    match = baseline.get("fingerprint") == recovered.get("fingerprint")
                    status_text = "MATCH (100% PARITY)" if match else "MISMATCH"
                    st.metric("Parity Check", status_text)

                st.caption(f"Fingerprint: `{recovered.get('fingerprint')[:24]}...`")

                # Baseline vs Recovered Bar Chart
                chart_df = pd.DataFrame(
                    [
                        {"Stage": "Baseline (Before Crash)", "Rows": baseline.get("row_count", 0)},
                        {
                            "Stage": "Recovered (After Recreate)",
                            "Rows": recovered.get("row_count", 0),
                        },
                    ]
                )
                st.bar_chart(chart_df, x="Stage", y="Rows", color="#3B82F6")
            else:
                st.info("Snapshot records are missing for this run.")

    with right_col:
        render_volume_inspector_card(vol_health)

        st.markdown("### Latest Verified Backup")
        if not latest_backup:
            render_empty_state(
                title="No Backups Recorded",
                message="No logical backup archives exist in the metadata store.",
                action_label="Go to Backup & Restore →",
            )
        else:
            st.markdown(
                f"**Archive:** `{latest_backup.get('filename')}`  \n"
                f"**Size:** {latest_backup.get('size_bytes', 0):,} bytes  \n"
                f"**Created:** {latest_backup.get('created_at')}  \n"
                f"**Status:** `{latest_backup.get('status')}`"
            )

    st.divider()

    # 4. Active Alerts & Recent Events
    alert_col, event_col = st.columns([1, 1])

    with alert_col:
        st.markdown("### Operational Alerts")
        alerts = get_active_alerts()
        firing_alerts = [a for a in alerts if a.get("state") == "FIRING"]
        if not firing_alerts:
            st.success("✓ All system alerts are in normal state (no firing alerts).")
        else:
            for al in firing_alerts:
                st.error(f"⚠️ **{al.get('rule_id')}**: {al.get('details', 'Condition met.')}")

    with event_col:
        st.markdown("### Recent Audit Trail")
        events = get_recent_audit_events(limit=5)
        if not events:
            st.info("No audit events recorded.")
        else:
            for ev in events:
                st.caption(
                    f"**{ev.get('timestamp_utc')}** — `{ev.get('operation')}`: {ev.get('status')} "
                    f"({ev.get('duration_ms', 0)}ms)"
                )
