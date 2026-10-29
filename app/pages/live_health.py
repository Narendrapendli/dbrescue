"""Live Health Page for dbrescue Dashboard.

Visualizes real-time database connectivity, timed latency, container lifecycle state,
CPU and memory consumption from Docker stats API, active MySQL threads, and telemetry freshness.
"""

from __future__ import annotations

import streamlit as st

from app.components import render_kpi_card, render_volume_inspector_card
from app.services import (
    evaluate_alerts,
    get_container_health,
    get_database_health,
    get_volume_health,
)
from app.theme import COLOR_FAILED, COLOR_PENDING, COLOR_SUCCESS, COLOR_UNKNOWN


def render_live_health_page() -> None:
    """Render the real-time operational health and telemetry monitor."""
    st.title("Real-Time Telemetry & Infrastructure Posture")
    st.caption("Live health inspection, latency monitoring, Docker resource telemetry, and freshness tracking.")

    # Refresh bar
    c_btn, c_note = st.columns([1, 4])
    with c_btn:
        st.button("🔄 Refresh Telemetry Now", type="primary")
    with c_note:
        st.caption("All metrics are sampled directly from MySQL and Docker socket APIs. No synthetic data.")

    st.divider()

    # 1. Real-time Telemetry Ribbon
    db_h = get_database_health()
    cntr_h = get_container_health()
    vol_h = get_volume_health()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        is_conn = db_h.get("reachable", False)
        lat = db_h.get("latency_ms")
        lat_display = f"{lat:.2f} ms" if lat is not None else "UNKNOWN"
        accent = COLOR_SUCCESS if is_conn else COLOR_FAILED
        render_kpi_card(
            title="Database Ping Latency",
            value=lat_display,
            subtitle="Real timed 'SELECT 1;' query",
            accent_color=accent,
        )

    with col2:
        threads = db_h.get("threads_connected", 0)
        t_display = str(threads) if is_conn else "UNKNOWN"
        render_kpi_card(
            title="Active Connections",
            value=t_display,
            subtitle="SHOW STATUS Threads_connected",
            accent_color=COLOR_SUCCESS if is_conn else COLOR_UNKNOWN,
        )

    with col3:
        cpu = cntr_h.get("cpu_percent")
        cpu_display = f"{cpu:.1f}%" if cpu is not None else "UNKNOWN"
        c_acc = COLOR_SUCCESS if cpu is not None and cpu < 80.0 else (COLOR_PENDING if cpu else COLOR_UNKNOWN)
        render_kpi_card(
            title="Container CPU Usage",
            value=cpu_display,
            subtitle="Docker Stats API computation",
            accent_color=c_acc,
        )

    with col4:
        mem_h = cntr_h.get("memory_human", "UNKNOWN")
        render_kpi_card(
            title="Memory Allocation",
            value=mem_h,
            subtitle="Working set RSS bytes",
            accent_color=COLOR_SUCCESS if mem_h != "UNKNOWN" else COLOR_UNKNOWN,
        )

    st.divider()

    # 2. Detailed Infrastructure Cards & Mount Inspector
    col_l, col_r = st.columns([1.2, 1])

    with col_l:
        st.markdown("### Engine & Container Detailed State")
        st.markdown(
            f"""
            - **MySQL Reachable:** `{'YES' if is_conn else 'NO'}`
            - **Server Version:** `{db_h.get('version', 'UNKNOWN')}`
            - **Docker Daemon Online:** `{'YES' if cntr_h.get('docker_available') else 'NO'}`
            - **Container Lifecycle State:** `{cntr_h.get('state', 'UNKNOWN')}`
            - **Container Healthcheck Status:** `{cntr_h.get('health_status', 'UNKNOWN')}`
            """
        )
        if cntr_h.get("error"):
            st.warning(f"Container notice: {cntr_h.get('error')}")
        if db_h.get("error"):
            st.error(f"Database notice: {db_h.get('error')}")

    with col_r:
        render_volume_inspector_card(vol_h)

    st.divider()

    # 3. Alert Rule Evaluations
    st.markdown("### Active Rule Diagnostics")
    evals = evaluate_alerts()

    rows = []
    for ev in evals:
        rows.append({
            "Alert Rule": ev.rule_name,
            "State": ev.state.value if hasattr(ev.state, "value") else str(ev.state),
            "Severity": ev.severity.value if hasattr(ev.severity, "value") else str(ev.severity),
            "Diagnostic Message": ev.message,
            "Has Evidence": "YES" if getattr(ev, "has_evidence", False) else "NO",
        })

    st.dataframe(rows, use_container_width=True)
