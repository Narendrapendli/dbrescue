"""Timeline component for dbrescue.

Renders an enterprise audit trail and event timeline.
"""

from __future__ import annotations

import streamlit as st

from app.theme import BORDER_COLOR, CARD_BG, COLOR_UNKNOWN, STATUS_COLOR_MAP


def render_timeline(events: list[dict]) -> None:
    """Render a chronological audit event timeline."""
    st.markdown("### Operational Event Timeline")

    if not events:
        st.info("No audit events recorded yet.")
        return

    for evt in events:
        timestamp = evt.get("timestamp_utc", "UNKNOWN")
        op = evt.get("operation", "EVENT")
        status = evt.get("status", "UNKNOWN").upper()
        duration_ms = evt.get("duration_ms")
        details = evt.get("details", "")
        error = evt.get("error_message")
        color = STATUS_COLOR_MAP.get(status, COLOR_UNKNOWN)

        dur_text = f"• {duration_ms} ms" if duration_ms is not None else ""
        err_html = f"<div style='color: #F87171; margin-top: 0.25rem; font-family: monospace; font-size: 0.75rem;'>{error}</div>" if error else ""

        html = f"""
        <div style="
            background-color: {CARD_BG};
            border-left: 3px solid {color};
            border-top: 1px solid {BORDER_COLOR};
            border-right: 1px solid {BORDER_COLOR};
            border-bottom: 1px solid {BORDER_COLOR};
            border-radius: 4px;
            padding: 0.65rem 0.95rem;
            margin-bottom: 0.45rem;
            font-size: 0.85rem;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <span style="font-weight: 600; color: #E5E7EB;">{op}</span>
                <span style="color: #9CA3AF; font-size: 0.75rem;">{timestamp} {dur_text}</span>
            </div>
            <div style="color: #9CA3AF; margin-top: 0.2rem; font-size: 0.8rem;">
                Status: <span style="color: {color}; font-weight: 600;">{status}</span>
                {f" — {details}" if details else ""}
            </div>
            {err_html}
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)
