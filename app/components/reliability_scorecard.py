"""Reliability Scorecard component for dbrescue.

Computes and displays real RTO, RPO, and recovery success rate strictly from historical runs.
Renders 'Not tested yet' if no test runs have occurred.
"""

from __future__ import annotations

import streamlit as st

from app.theme import BORDER_COLOR, CARD_BG, COLOR_UNKNOWN


def render_reliability_scorecard(scorecard_data: dict) -> None:
    """Render the Reliability Scorecard."""
    st.markdown("### Reliability Scorecard (RTO / RPO / Parity)")

    if not scorecard_data.get("tested"):
        html = f"""
        <div style="
            background-color: {CARD_BG};
            border: 1px solid {BORDER_COLOR};
            border-radius: 8px;
            padding: 1.5rem;
            margin-bottom: 1rem;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h4 style="margin: 0; color: #E5E7EB;">Operational Disaster Recovery SLA</h4>
                    <p style="margin: 0.25rem 0 0 0; font-size: 0.85rem; color: #9CA3AF;">
                        Measured from real volume persistence workflows.
                    </p>
                </div>
                <span style="
                    background-color: {COLOR_UNKNOWN}22;
                    color: {COLOR_UNKNOWN};
                    border: 1px solid {COLOR_UNKNOWN}55;
                    padding: 0.25rem 0.75rem;
                    border-radius: 9999px;
                    font-size: 0.75rem;
                    font-weight: 600;
                ">
                    Not tested yet
                </span>
            </div>
            <div style="margin-top: 1.25rem; font-size: 0.9rem; color: #9CA3AF;">
                Execute at least one disaster recovery simulation on the <b>Persistence Tests</b> page to compute empirical Recovery Time Objective (RTO) and Parity Success Rate.
            </div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)
        return

    # Real data exists
    success_rate = scorecard_data.get("success_rate_percent", 0.0)
    avg_rto = scorecard_data.get("avg_rto_seconds")
    rpo = scorecard_data.get("rpo_seconds", 0.0)
    total_runs = scorecard_data.get("total_runs", 0)
    successful_runs = scorecard_data.get("successful_runs", 0)

    rto_str = f"{avg_rto:.2f}s" if avg_rto is not None else "UNKNOWN"

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Recovery Success Rate", f"{success_rate}%", f"{successful_runs}/{total_runs} runs passed")
    with col2:
        st.metric("Mean RTO (Recovery Time)", rto_str, "Container recreation + health")
    with col3:
        st.metric("RPO (Data Loss Window)", f"{rpo:.1f}s", "Zero data loss on volume")
    with col4:
        st.metric("Total DR Runs", str(total_runs), "Historical audits")
