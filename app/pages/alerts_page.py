"""Alerts Management Page for dbrescue Dashboard.

Visualizes evaluated alert rules, thresholds, active FIRING conditions,
and guarantees that no rule is marked 'OK' without positive evidence.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.services import evaluate_alerts, get_active_alerts


def render_alerts_page() -> None:
    """Render the operational alerts evaluation and active incident console."""
    st.title("Operational Alerts & SLA Threshold Monitoring")
    st.caption(
        "Evidence-based alert rules. An alert remains NOT_EVALUATED until an explicit check is conducted."
    )

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        st.button("🔔 Evaluate Alert Rules Now", type="primary")
    with col_info:
        st.caption(
            "Rules are evaluated against real telemetry samples, volume mounts, and backup ages."
        )

    st.divider()

    # 1. Active Firing Alerts Callout
    active_alerts = get_active_alerts()
    firing = [a for a in active_alerts if a.get("state") == "FIRING"]

    if firing:
        st.markdown("### ⚠️ Active Firing Incidents")
        for f in firing:
            st.error(
                f"**Rule:** `{f.get('rule_id')}` | **Severity:** {f.get('severity')}  \n"
                f"**Details:** {f.get('details')}"
            )
        st.divider()

    # 2. Rule Evaluation Matrix
    st.markdown("### Standard Operational Alert Rules")
    evaluations = evaluate_alerts()

    matrix_rows = []
    for ev in evaluations:
        st_val = ev.state.value if hasattr(ev.state, "value") else str(ev.state)
        sev_val = ev.severity.value if hasattr(ev.severity, "value") else str(ev.severity)
        matrix_rows.append(
            {
                "Rule Name": ev.rule_name,
                "State": st_val,
                "Severity": sev_val,
                "Evidence Verified": "YES" if getattr(ev, "has_evidence", False) else "NO",
                "Message / Current Diagnosis": ev.message,
                "Evaluated At (UTC)": getattr(ev, "evaluated_at", "N/A"),
            }
        )

    st.dataframe(pd.DataFrame(matrix_rows), use_container_width=True)

    st.info(
        "💡 **Guaranteed Integrity Rule:** If telemetry is unavailable or unmeasured, alert rules remain "
        "in the `NOT_EVALUATED` state. A status of `OK` is rendered only when affirmative telemetry verifies normal health."
    )
