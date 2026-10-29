"""Empty state component for dbrescue.

Renders clean, neutral placeholders when telemetry or historical test runs are absent.
Never fabricates synthetic data.
"""

from __future__ import annotations

import streamlit as st

from app.theme import BORDER_COLOR, CARD_BG, TEXT_MUTED


def render_empty_state(
    title: str,
    message: str,
    action_label: str | None = None,
) -> None:
    """Render a neutral empty state card."""
    action_html = (
        f'<div style="margin-top: 1rem; font-size: 0.85rem; color: #3B82F6; font-weight: 500;">{action_label}</div>'
        if action_label
        else ""
    )

    html = f"""
    <div style="
        background-color: {CARD_BG};
        border: 1px dashed {BORDER_COLOR};
        border-radius: 8px;
        padding: 2.5rem 1.5rem;
        text-align: center;
        margin: 1rem 0;
    ">
        <div style="font-size: 2rem; margin-bottom: 0.5rem; color: {TEXT_MUTED};">📂</div>
        <div style="font-size: 1.1rem; font-weight: 600; color: #E5E7EB; margin-bottom: 0.35rem;">
            {title}
        </div>
        <div style="font-size: 0.85rem; color: {TEXT_MUTED}; max-width: 480px; margin: 0 auto;">
            {message}
        </div>
        {action_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
