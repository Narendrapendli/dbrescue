"""Recovery Timeline & Audit Logs Page for dbrescue Dashboard.

Forensic event timeline, structured audit log browsing, and multi-parameter filtering.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components import render_empty_state, render_timeline
from app.services import get_persistence_runs, get_recent_audit_events


def render_timeline_page() -> None:
    """Render the visual timeline view of recent operational events."""
    st.title("Disaster Recovery Execution Timeline")
    st.caption("Chronological progression of recovery stages, state machine transitions, and forensics.")

    events = get_recent_audit_events(limit=50)
    if not events:
        render_empty_state(
            title="No Events Found",
            message="No audit trail events have been captured yet.",
        )
    else:
        render_timeline(events)


def render_audit_logs_page() -> None:
    """Render the searchable and filterable operational audit trail log table."""
    st.title("Operational Audit Trail & Forensic Explorer")
    st.caption("Immutable append-only operational events stored with foreign key integrity in SQLite WAL mode.")

    # 1. Filters Ribbon
    f_col1, f_col2, f_col3 = st.columns(3)

    runs = get_persistence_runs(limit=50)
    run_options = ["All Runs"] + [str(r["id"]) for r in runs]

    with f_col1:
        sel_run = st.selectbox("Filter by Run ID", options=run_options)
    with f_col2:
        op_filter = st.text_input("Filter by Operation / Event Type", placeholder="e.g. STAGE_TRANSITION")
    with f_col3:
        status_filter = st.selectbox("Filter by Status / Severity", options=["All", "INFO", "WARNING", "ERROR", "CRITICAL"])

    run_id_val = int(sel_run) if sel_run != "All Runs" else None
    op_val = op_filter.strip() if op_filter.strip() else None
    status_val = status_filter if status_filter != "All" else None

    # Fetch filtered events
    events = get_recent_audit_events(
        limit=100,
        run_id=run_id_val,
        operation=op_val,
        status=status_val,
    )

    st.divider()

    if not events:
        render_empty_state(
            title="No Matching Audit Events",
            message="No operational events matched your filter criteria.",
        )
    else:
        st.markdown(f"**Found {len(events)} event record(s):**")
        rows = []
        for e in events:
            rows.append({
                "ID": e.get("id"),
                "Timestamp (UTC)": e.get("created_at") or e.get("timestamp_utc"),
                "Run ID": e.get("run_id") or "N/A",
                "Stage": e.get("stage", "N/A"),
                "Operation": e.get("event_type") or e.get("operation"),
                "Severity": e.get("severity") or e.get("status"),
                "Duration (ms)": round(e.get("duration_ms") or 0.0, 1),
                "Message": e.get("message") or e.get("details"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
