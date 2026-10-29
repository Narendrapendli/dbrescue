"""Backup & Restore Page for dbrescue Dashboard.

Logical backup generation, cryptographic checksum verification, isolated dry-run restore
parity validation, and safety-guarded retention policy management.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.components import render_empty_state
from app.services import (
    apply_retention,
    execute_backup,
    execute_isolated_restore,
    get_backups,
    get_retention_policy,
    verify_backup_file,
)


def render_backup_restore_page() -> None:
    """Render the Backup, Verification, Restore, and Retention Management interface."""
    st.title("Backup, Cryptographic Verification & Parity Restore")
    st.caption("Manage logical mysqldump archives, SHA-256 checksums, isolated dry-runs, and retention policies.")

    # 1. Create Logical Backup Section
    with st.expander("💾 Create Logical MySQL Backup", expanded=False):
        st.markdown(
            "Triggers `mysqldump` executed inside the target container with `--single-transaction`, "
            "`--routines`, `--triggers`, and `--events`. Zero credentials on CLI. Streams through GZIP "
            "and computes cryptographic SHA-256 digest."
        )
        col_lbl, col_btn = st.columns([3, 1])
        with col_lbl:
            backup_label = st.text_input("Backup Label / Description", value="manual-dashboard-backup")
        with col_btn:
            st.markdown("<div style='margin-top: 1.75rem;'></div>", unsafe_allow_html=True)
            btn_create = st.button("Trigger Backup", type="primary")

        if btn_create:
            with st.spinner("Streaming compressed database backup..."):
                try:
                    meta = execute_backup(label=backup_label)
                    st.success(
                        f"✓ Backup created successfully! Archive: `{meta.filename}` "
                        f"({meta.file_size_bytes:,} bytes). Status: `{meta.status}`."
                    )
                except Exception as exc:
                    st.error(f"Backup generation failed: {exc}")

    st.divider()

    # 2. Backup Inventory Table
    st.markdown("### Backup Archive Inventory")
    backups = get_backups(limit=50)

    if not backups:
        render_empty_state(
            title="No Backups Found",
            message="No backup archives have been generated yet. Use the section above to create a logical backup.",
        )
    else:
        table_rows = []
        for b in backups:
            table_rows.append({
                "ID": b.get("id"),
                "Filename": b.get("filename"),
                "Size (Bytes)": f"{b.get('file_size_bytes', 0):,}",
                "SHA-256": (b.get("sha256_checksum") or "")[:16] + "...",
                "Status": b.get("status"),
                "Created At (UTC)": b.get("created_at"),
            })
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True)

        # 3. Actions on Selected Backup (Verify Checksum & Isolated Restore)
        st.divider()
        b_col1, b_col2 = st.columns(2)

        backup_ids = [b["id"] for b in backups]

        with b_col1:
            st.markdown("#### Cryptographic Checksum Verification")
            st.caption("Recomputes file SHA-256 digest from disk to ensure zero bit-rot or tampering.")
            v_b_id = st.selectbox("Select Backup to Verify", options=backup_ids, key="sel_verify_id")
            if st.button("Recompute & Verify Checksum", key="btn_verify_chk"):
                with st.spinner("Reading archive and recomputing SHA-256 digest..."):
                    try:
                        ok = verify_backup_file(v_b_id)
                        if ok:
                            st.success(f"✓ Checksum verified! Status updated to 'VERIFIED_FILE' for Backup #{v_b_id}.")
                        else:
                            st.error(f"❌ Checksum verification failed for Backup #{v_b_id}.")
                    except Exception as exc:
                        st.error(f"Verification error: {exc}")

        with b_col2:
            st.markdown("#### Isolated Dry-Run Restore")
            st.caption(
                "Restores archive into an isolated sandbox database (`dbrescue_restore_<id>`), verifies 100% "
                "row & fingerprint parity, and defensively drops the sandbox database afterward."
            )
            r_b_id = st.selectbox("Select Backup to Restore", options=backup_ids, key="sel_restore_id")
            required_token = f"RESTORE-{r_b_id}"
            typed_restore_token = st.text_input(
                f"Type confirmation token ({required_token}):",
                key="input_restore_token",
                placeholder=required_token,
            )

            if st.button("🚀 Run Isolated Restore Test", type="secondary"):
                if typed_restore_token.strip() != required_token:
                    st.error(f"Safety token mismatch: You must type '{required_token}' exactly.")
                else:
                    with st.spinner("Executing isolated restore and verifying row parity..."):
                        try:
                            res = execute_isolated_restore(r_b_id, confirm_token=typed_restore_token)
                            if res.verification_status == "VERIFIED" or res.status.value == "SUCCESS":
                                st.success(
                                    f"🎉 Restore Parity Verified! Rows Restored: {res.rows_restored:,} "
                                    f"in {res.duration_seconds:.2f}s. Sandbox database dropped safely."
                                )
                            else:
                                st.error(f"❌ Restore completed with verification status: {res.verification_status}.")
                        except Exception as exc:
                            st.error(f"Isolated restore failed: {exc}")

    st.divider()

    # 4. Retention Policy Management
    st.markdown("### Backup Retention Policy")
    policy = get_retention_policy()

    col_ret1, col_ret2, col_ret3 = st.columns(3)
    with col_ret1:
        keep_last = st.number_input(
            "Preserve Minimum Verified Backups",
            min_value=1,
            max_value=100,
            value=int(policy.get("keep_last", 5)),
        )
    with col_ret2:
        max_age = st.number_input(
            "Maximum Backup Age (Days)",
            min_value=1,
            max_value=365,
            value=int(policy.get("max_age_days", 30)),
        )
    with col_ret3:
        st.markdown("<div style='margin-top: 1.75rem;'></div>", unsafe_allow_html=True)
        btn_dry_run = st.button("🔍 Preview Retention (Dry Run)")

    if btn_dry_run:
        with st.spinner("Evaluating retention rules across archives..."):
            try:
                res = apply_retention(keep_last=int(keep_last), max_age_days=int(max_age), dry_run=True)
                st.info(
                    f"**Dry Run Evaluation Results:**  \n"
                    f"• Evaluated: {res.evaluated_count} backup(s)  \n"
                    f"• Would Purge: {res.deleted_count} backup(s)  \n"
                    f"• Preserved: {res.preserved_count} backup(s)  \n"
                    f"*(Newest verified backup is permanently protected)*"
                )
            except Exception as exc:
                st.error(f"Retention preview error: {exc}")
