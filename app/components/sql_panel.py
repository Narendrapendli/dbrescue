"""SQL Verification Panel component for dbrescue.

Provides a read-only query explorer restricted to an allowlist of diagnostic queries.
Disallows arbitrary query submission.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.services import execute_allowlisted_query, get_allowlisted_queries


def render_sql_panel() -> None:
    """Render the allowlisted read-only SQL execution panel."""
    st.markdown("### Read-Only Verification Console")
    st.caption("Execute allowlisted verification queries against the active MySQL instance.")

    queries = get_allowlisted_queries()
    query_options = list(queries.keys())

    selected_query_title = st.selectbox("Select Allowlisted Verification Query", query_options)

    if selected_query_title:
        sql = queries[selected_query_title]
        st.code(sql, language="sql")

        if st.button("Execute Query", key="btn_run_sql"):
            with st.spinner("Executing query..."):
                try:
                    rows = execute_allowlisted_query(selected_query_title)
                    if rows:
                        df = pd.DataFrame(rows)
                        st.dataframe(df, use_container_width=True)
                        st.success(f"Returned {len(rows)} record(s).")
                    else:
                        st.info("Query returned 0 rows.")
                except Exception as exc:
                    st.error(f"Execution failed: {exc}")
