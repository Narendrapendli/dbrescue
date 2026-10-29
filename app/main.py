"""Main Entrypoint for dbrescue Streamlit Application.

Dark navy themed operational console for MySQL Docker volume disaster recovery,
integrity verification, real-time telemetry, and audit reporting.
"""

from __future__ import annotations

import streamlit as st

# Configure page settings as early as possible
st.set_page_config(
    page_title="dbrescue | Disaster Recovery",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app.services import (  # noqa: E402
    get_config,
    get_container_health,
    get_database_health,
    get_volume_health,
)
from app.theme import apply_theme  # noqa: E402

# Apply CSS styling
apply_theme()


def render_sidebar() -> str:
    """Render the persistent sidebar navigation and real-time system status."""
    st.sidebar.markdown(
        """
        <div style="padding: 0.5rem 0 1rem 0;">
            <div style="font-size: 1.4rem; font-weight: 800; color: #3B82F6; letter-spacing: -0.02em;">
                🛡️ dbrescue
            </div>
            <div style="font-size: 0.75rem; color: #9CA3AF; margin-top: 0.15rem;">
                MySQL 8.4 LTS Volume DR Platform
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    nav_options = [
        "📊 Overview",
        "🚀 Persistence Tests",
        "🩺 Live Health",
        "💾 Backup & Restore",
        "📜 Recovery Timeline",
        "⚠️ Alerts",
        "🔒 Audit Logs",
        "⚙️ Configuration & Reports",
    ]

    selected_page = st.sidebar.radio("Navigation", nav_options, index=0)

    st.sidebar.markdown("---")
    st.sidebar.markdown("### System Posture")

    # Real-time infrastructure indicators
    db_h = get_database_health()
    cntr_h = get_container_health()
    vol_h = get_volume_health()

    db_status = "OK" if db_h.get("reachable") else "FAILED"
    st.sidebar.caption(f"**Database:** {db_status}")
    st.sidebar.caption(f"**Container:** {cntr_h.get('state', 'UNKNOWN')}")
    st.sidebar.caption(f"**Volume Mount:** {vol_h.get('volume_name', 'mysql_data')}")

    cfg = get_config()
    st.sidebar.markdown("---")
    st.sidebar.caption(f"Environment: `{cfg.environment}`")
    st.sidebar.caption(f"Target Container: `{cfg.docker.primary_container_name}`")

    return selected_page


def main() -> None:
    """Main routing controller."""
    page = render_sidebar()

    if page == "📊 Overview":
        from app.pages.overview import render_overview_page
        render_overview_page()
    elif page == "🚀 Persistence Tests":
        from app.pages.persistence_tests import render_persistence_tests_page
        render_persistence_tests_page()
    elif page == "🩺 Live Health":
        try:
            from app.pages.live_health import render_live_health_page
            render_live_health_page()
        except ImportError:
            st.info("Live Health module will be activated in Prompt 9.")
    elif page == "💾 Backup & Restore":
        try:
            from app.pages.backup_restore import render_backup_restore_page
            render_backup_restore_page()
        except ImportError:
            st.info("Backup & Restore module will be activated in Prompt 9.")
    elif page == "📜 Recovery Timeline":
        try:
            from app.pages.timeline_audit import render_timeline_page
            render_timeline_page()
        except ImportError:
            st.info("Recovery Timeline module will be activated in Prompt 9.")
    elif page == "⚠️ Alerts":
        try:
            from app.pages.alerts_page import render_alerts_page
            render_alerts_page()
        except ImportError:
            st.info("Alerts module will be activated in Prompt 9.")
    elif page == "🔒 Audit Logs":
        try:
            from app.pages.timeline_audit import render_audit_logs_page
            render_audit_logs_page()
        except ImportError:
            st.info("Audit Logs module will be activated in Prompt 9.")
    elif page == "⚙️ Configuration & Reports":
        try:
            from app.pages.configuration_reports import render_configuration_reports_page
            render_configuration_reports_page()
        except ImportError:
            st.info("Configuration & Reports module will be activated in Prompt 9.")


if __name__ == "__main__":
    main()
