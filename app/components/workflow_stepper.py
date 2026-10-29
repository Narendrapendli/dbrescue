"""Workflow stepper component for dbrescue.

Renders the 18 disaster recovery workflow steps with live progress indicator.
"""

from __future__ import annotations

import streamlit as st

from app.theme import (
    BORDER_COLOR,
    CARD_BG,
    COLOR_FAILED,
    COLOR_RUNNING,
    COLOR_SUCCESS,
    COLOR_UNKNOWN,
)

STAGES = [
    ("INIT_RUN", "Initialize Run & Acquire Lock"),
    ("VERIFY_ENVIRONMENT", "Verify Configuration & Environment"),
    ("VERIFY_DOCKER", "Verify Docker Daemon Reachability"),
    ("VERIFY_VOLUME", "Verify Named Volume & Mountpoint"),
    ("VERIFY_CONTAINER", "Verify Target MySQL Container"),
    ("CHECK_INITIAL_HEALTH", "Check Initial MySQL Health"),
    ("GENERATE_RUN_ID", "Generate Cryptographic Run ID"),
    ("SEED_TEST_DATA", "Create Run-Scoped Test Records"),
    ("CAPTURE_BASELINE", "Capture Baseline Count & Fingerprint"),
    ("PERSIST_BASELINE", "Persist Baseline to SQLite"),
    ("STOP_CONTAINER", "Safely Stop MySQL Container"),
    ("REMOVE_CONTAINER", "Remove Container (Preserve Volume)"),
    ("RECREATE_CONTAINER", "Recreate Container with Same Volume"),
    ("WAIT_HEALTHY", "Wait Until Recreated Container Healthy"),
    ("CAPTURE_RECOVERED", "Query MySQL & Fingerprint Recovered"),
    ("VERIFY_INTEGRITY", "Compare Baseline vs Recovered"),
    ("PERSIST_RESULTS", "Persist Results, Audit Log & Report"),
    ("RELEASE_LOCK", "Release File Lock & Finalize"),
]


def render_workflow_stepper(current_stage: str | None = None, is_failed: bool = False) -> None:
    """Render the 18 stages showing completed, in-progress, or failed state."""
    stage_names = [s[0] for s in STAGES]
    current_idx = stage_names.index(current_stage) if current_stage in stage_names else -1

    st.markdown("### 18-Stage Disaster Recovery Workflow")

    rows = []
    for idx, (_code, title) in enumerate(STAGES):
        step_num = idx + 1
        if current_idx == -1:
            icon = "○"
            color = COLOR_UNKNOWN
            status_txt = "Pending"
        elif idx < current_idx:
            icon = "✓"
            color = COLOR_SUCCESS
            status_txt = "Completed"
        elif idx == current_idx:
            if is_failed:
                icon = "✗"
                color = COLOR_FAILED
                status_txt = "Failed"
            else:
                icon = "▶"
                color = COLOR_RUNNING
                status_txt = "In Progress"
        else:
            icon = "○"
            color = COLOR_UNKNOWN
            status_txt = "Pending"

        row = f"""
        <div style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0.4rem 0.75rem;
            margin-bottom: 0.25rem;
            background-color: {CARD_BG};
            border-left: 3px solid {color};
            border-radius: 4px;
            font-size: 0.85rem;
        ">
            <div style="display: flex; align-items: center; gap: 0.75rem;">
                <span style="color: {color}; font-weight: bold; width: 1.2rem; text-align: center;">{icon}</span>
                <span style="color: #9CA3AF; width: 1.5rem;">{step_num:02d}.</span>
                <span style="font-weight: 500; color: #E5E7EB;">{title}</span>
            </div>
            <span style="color: {color}; font-size: 0.75rem; font-weight: 600;">{status_txt}</span>
        </div>
        """
        rows.append(row)

    container_html = f"""
    <div style="border: 1px solid {BORDER_COLOR}; border-radius: 8px; padding: 0.75rem; background-color: #0A0F1D;">
        {"".join(rows)}
    </div>
    """
    st.markdown(container_html, unsafe_allow_html=True)
