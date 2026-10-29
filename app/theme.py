"""Dashboard Theme & Styling for dbrescue.

Adheres strictly to the dark navy design language:
Background: #0A0F1D, Cards: #111827, Border: #1F2937, Accents: #3B82F6, #10B981, #F59E0B, #EF4444.
"""

from __future__ import annotations

import streamlit as st

PRIMARY_COLOR = "#3B82F6"
BG_DARK = "#0A0F1D"
CARD_BG = "#111827"
BORDER_COLOR = "#1F2937"
TEXT_PRIMARY = "#F3F4F6"
TEXT_MUTED = "#9CA3AF"

# Palette for Statuses
COLOR_SUCCESS = "#10B981"
COLOR_FAILED = "#EF4444"
COLOR_RUNNING = "#3B82F6"
COLOR_PENDING = "#F59E0B"
COLOR_UNKNOWN = "#6B7280"
COLOR_PURPLE = "#8B5CF6"

STATUS_COLOR_MAP = {
    "SUCCESS": COLOR_SUCCESS,
    "RESTORE_VERIFIED": COLOR_SUCCESS,
    "VERIFIED_FILE": COLOR_SUCCESS,
    "OK": COLOR_SUCCESS,
    "FAILED": COLOR_FAILED,
    "FIRING": COLOR_FAILED,
    "ERROR": COLOR_FAILED,
    "RUNNING": COLOR_RUNNING,
    "PENDING": COLOR_PENDING,
    "DEGRADED": COLOR_PENDING,
    "NOT_EVALUATED": COLOR_UNKNOWN,
    "UNKNOWN": COLOR_UNKNOWN,
    "SKIPPED": COLOR_UNKNOWN,
    "STALE": COLOR_PENDING,
    "FRESH": COLOR_SUCCESS,
}


def apply_theme() -> None:
    """Inject global CSS rules to ensure exact visual alignment with the dark navy theme."""
    custom_css = f"""
    <style>
    /* Main body styling */
    .stApp {{
        background-color: {BG_DARK};
        color: {TEXT_PRIMARY};
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }}

    /* Sidebar styling */
    section[data-testid="stSidebar"] {{
        background-color: #0d1322 !important;
        border-right: 1px solid {BORDER_COLOR};
    }}

    /* Card container */
    .kpi-card {{
        background-color: {CARD_BG};
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
        padding: 1.25rem;
        margin-bottom: 1rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -1px rgba(0, 0, 0, 0.2);
    }}
    .kpi-card:hover {{
        border-color: #374151;
    }}
    .kpi-title {{
        color: {TEXT_MUTED};
        font-size: 0.825rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.35rem;
    }}
    .kpi-value {{
        font-size: 1.75rem;
        font-weight: 700;
        color: {TEXT_PRIMARY};
        line-height: 1.2;
    }}
    .kpi-subtitle {{
        font-size: 0.775rem;
        color: {TEXT_MUTED};
        margin-top: 0.35rem;
    }}

    /* Status badge */
    .status-badge {{
        display: inline-flex;
        align-items: center;
        padding: 0.2rem 0.65rem;
        border-radius: 9999px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.025em;
        line-height: 1;
    }}

    /* Alert callouts */
    .alert-box {{
        padding: 0.85rem 1.15rem;
        border-radius: 6px;
        margin-bottom: 0.85rem;
        border-left: 4px solid;
    }}
    .alert-box-info {{
        background-color: rgba(59, 130, 246, 0.1);
        border-color: {COLOR_RUNNING};
        color: #93C5FD;
    }}
    .alert-box-success {{
        background-color: rgba(16, 185, 129, 0.1);
        border-color: {COLOR_SUCCESS};
        color: #A7F3D0;
    }}
    .alert-box-warning {{
        background-color: rgba(245, 158, 11, 0.1);
        border-color: {COLOR_PENDING};
        color: #FDE68A;
    }}
    .alert-box-danger {{
        background-color: rgba(239, 68, 68, 0.1);
        border-color: {COLOR_FAILED};
        color: #FECACA;
    }}

    /* Tables & DataFrames */
    .stDataFrame, .stTable {{
        background-color: {CARD_BG} !important;
        border-radius: 6px;
        border: 1px solid {BORDER_COLOR};
    }}

    /* Headers */
    h1, h2, h3, h4 {{
        color: {TEXT_PRIMARY} !important;
        font-weight: 600 !important;
    }}

    /* Progress bar */
    .stProgress > div > div > div > div {{
        background-color: {PRIMARY_COLOR} !important;
    }}
    </style>
    """
    st.markdown(custom_css, unsafe_allow_html=True)
