"""KPI card component for dbrescue.

Renders dark-navy themed metrics cards with primary values, labels, and subtitles.
"""

from __future__ import annotations

import streamlit as st

from app.theme import BORDER_COLOR, CARD_BG, PRIMARY_COLOR, TEXT_MUTED, TEXT_PRIMARY


def render_kpi_card(
    title: str,
    value: str | int | float,
    subtitle: str | None = None,
    accent_color: str = PRIMARY_COLOR,
    border_color: str = BORDER_COLOR,
) -> None:
    """Render a styled KPI card."""
    display_value = str(value) if value is not None else "UNKNOWN"
    sub_html = (
        f'<div style="font-size: 0.775rem; color: {TEXT_MUTED}; margin-top: 0.35rem;">{subtitle}</div>'
        if subtitle
        else ""
    )

    html = f"""
    <div style="
        background-color: {CARD_BG};
        border: 1px solid {border_color};
        border-top: 3px solid {accent_color};
        border-radius: 8px;
        padding: 1.15rem 1.25rem;
        margin-bottom: 0.75rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    ">
        <div style="
            color: {TEXT_MUTED};
            font-size: 0.8rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.3rem;
        ">
            {title}
        </div>
        <div style="
            font-size: 1.65rem;
            font-weight: 700;
            color: {TEXT_PRIMARY};
            line-height: 1.2;
        ">
            {display_value}
        </div>
        {sub_html}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
