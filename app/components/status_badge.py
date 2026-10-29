"""Status badge component for dbrescue.

Renders high-visibility status badges using the 5 core status categories.
"""

from __future__ import annotations

import streamlit as st

from app.theme import COLOR_UNKNOWN, STATUS_COLOR_MAP


def render_status_badge(status: str | None, label: str | None = None) -> None:
    """Render an HTML pill badge for a given status."""
    raw_status = (status or "UNKNOWN").upper().strip()
    color = STATUS_COLOR_MAP.get(raw_status, COLOR_UNKNOWN)
    display_label = label or raw_status.replace("_", " ")

    html = f"""
    <span style="
        background-color: {color}22;
        color: {color};
        border: 1px solid {color}55;
        padding: 0.25rem 0.65rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.03em;
        display: inline-flex;
        align-items: center;
        margin: 0.1rem 0;
    ">
        ● &nbsp;{display_label}
    </span>
    """
    st.markdown(html, unsafe_allow_html=True)


def get_status_badge_html(status: str | None, label: str | None = None) -> str:
    """Return raw HTML string of the badge for embedding in markdown tables or cards."""
    raw_status = (status or "UNKNOWN").upper().strip()
    color = STATUS_COLOR_MAP.get(raw_status, COLOR_UNKNOWN)
    display_label = label or raw_status.replace("_", " ")

    return (
        f'<span style="background-color: {color}22; color: {color}; border: 1px solid {color}55; '
        f'padding: 0.2rem 0.5rem; border-radius: 9999px; font-size: 0.72rem; font-weight: 600;">'
        f"● {display_label}</span>"
    )
