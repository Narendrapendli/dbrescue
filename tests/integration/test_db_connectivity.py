"""Integration tests for dbrescue.db connection factory, timeouts, retries, and queries."""

from __future__ import annotations

import pytest

from dbrescue.config import MySQLConnectionConfig
from dbrescue.db import (
    DatabaseUnavailable,
    create_connection_pool,
    execute_query,
    get_server_version,
    get_table_names,
    get_threads_connected,
    get_uptime,
    ping_database,
)


@pytest.fixture
def unreachable_config():
    """Config pointing to an unallocated loopback port to test connection failure handling."""
    return MySQLConnectionConfig(
        host="127.0.0.1",
        port=59999,  # Non-listening port
        user="test_user",
        password="test_password",
        database="non_existent_db",
        root_password="root_password",
        connect_timeout_sec=1,
    )


@pytest.fixture
def local_test_config():
    """Config pointing to local compose MySQL instance."""
    return MySQLConnectionConfig(
        host="127.0.0.1",
        port=3306,
        user="rescue_app",
        password="RescuePass2026!Secure",
        database="student_db",
        root_password="RootRescue2026!Emergency",
        connect_timeout_sec=2,
    )


@pytest.mark.integration
def test_connection_pool_unreachable_host_raises_database_unavailable(unreachable_config):
    """Verifies that attempting to connect to an unreachable MySQL instance triggers DatabaseUnavailable."""
    with pytest.raises(DatabaseUnavailable, match="Unable to establish database connection"):
        create_connection_pool(unreachable_config, max_retries=2, backoff_base_sec=0.1)


@pytest.mark.integration
def test_ping_database_unreachable(unreachable_config):
    """Verifies ping_database returns False gracefully when MySQL is down without unhandled crash."""
    assert ping_database(unreachable_config, timeout_sec=1) is False


@pytest.mark.integration
def test_live_mysql_connectivity(local_test_config):
    """Tests live connection pool against running Compose MySQL if available.

    If MySQL container is not running on 127.0.0.1:3306, test skips cleanly.
    """
    if not ping_database(local_test_config, timeout_sec=1):
        pytest.skip("MySQL container is not running on 127.0.0.1:3306. Skipping live integration query.")

    pool = create_connection_pool(local_test_config, max_retries=1)

    # 1. Parameterized query
    res = execute_query(pool, "SELECT 1 + %s AS sum_val", (41,))
    assert res.row_count == 1
    assert res.rows[0][0] == 42
    assert res.columns == ["sum_val"]

    # 2. Server version
    version = get_server_version(pool)
    assert "8.4" in version

    # 3. Server telemetry
    uptime = get_uptime(pool)
    assert uptime >= 0
    threads = get_threads_connected(pool)
    assert threads >= 1

    # 4. Table listing
    tables = get_table_names(pool, "student_db")
    assert "students" in tables
    assert "courses" in tables
    assert "enrollments" in tables

    pool.close()
