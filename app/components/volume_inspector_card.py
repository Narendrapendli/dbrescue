"""Volume Inspector component for dbrescue.

Renders volume verification details, driver, mountpoint, and safety indicators.
"""

from __future__ import annotations

import streamlit as st

from app.theme import BORDER_COLOR, CARD_BG, COLOR_FAILED, COLOR_SUCCESS


def render_volume_inspector_card(volume_info: dict) -> None:
    """Render the Volume Inspector status card."""
    exists = volume_info.get("exists", False)
    mounted_correctly = volume_info.get("mounted_correctly", False)
    violations = volume_info.get("violations", [])
    vol_name = volume_info.get("volume_name", "UNKNOWN")
    driver = volume_info.get("driver", "UNKNOWN")
    mountpoint = volume_info.get("mountpoint", "UNKNOWN")
    size_human = volume_info.get("size_human", "UNKNOWN")
    target_path = volume_info.get("target_path", "/var/lib/mysql")

    if mounted_correctly:
        status_label = "VERIFIED MOUNT"
        status_color = COLOR_SUCCESS
        badge_bg = f"{COLOR_SUCCESS}22"
    elif not exists:
        status_label = "VOLUME MISSING"
        status_color = COLOR_FAILED
        badge_bg = f"{COLOR_FAILED}22"
    else:
        status_label = "MIS-MOUNTED / VIOLATION"
        status_color = COLOR_FAILED
        badge_bg = f"{COLOR_FAILED}22"

    st.markdown("### Named Volume Inspector")

    html = f"""
    <div style="
        background-color: {CARD_BG};
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
        padding: 1.25rem;
        margin-bottom: 1rem;
    ">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
            <div style="font-size: 1.1rem; font-weight: 600; color: #E5E7EB;">
                📦 {vol_name}
            </div>
            <span style="
                background-color: {badge_bg};
                color: {status_color};
                border: 1px solid {status_color}55;
                padding: 0.25rem 0.75rem;
                border-radius: 9999px;
                font-size: 0.75rem;
                font-weight: 600;
            ">
                ● {status_label}
            </span>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; font-size: 0.85rem;">
            <div>
                <span style="color: #9CA3AF;">Driver:</span>
                <span style="color: #F3F4F6; font-family: monospace;">{driver}</span>
            </div>
            <div>
                <span style="color: #9CA3AF;">Container Mount:</span>
                <span style="color: #F3F4F6; font-family: monospace;">{target_path}</span>
            </div>
            <div>
                <span style="color: #9CA3AF;">Disk Allocation:</span>
                <span style="color: #F3F4F6;">{size_human}</span>
            </div>
            <div>
                <span style="color: #9CA3AF;">Host Mountpoint:</span>
                <div style="color: #D1D5DB; font-family: monospace; font-size: 0.75rem; word-break: break-all; margin-top: 0.2rem;">
                    {mountpoint}
                </div>
            </div>
        </div>
    """

    if violations:
        violation_items = "".join(f"<li style='margin-bottom: 0.25rem;'>{v}</li>" for v in violations)
        html += f"""
        <div style="margin-top: 1rem; padding: 0.75rem; background-color: rgba(239, 68, 68, 0.1); border-left: 3px solid {COLOR_FAILED}; border-radius: 4px;">
            <div style="font-weight: 600; color: #FCA5A5; font-size: 0.85rem;">Mount Safety Violations Detected:</div>
            <ul style="margin: 0.5rem 0 0 1rem; padding: 0; color: #FECACA; font-size: 0.8rem;">
                {violation_items}
            </ul>
        </div>
        """

    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)
