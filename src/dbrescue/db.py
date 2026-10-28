"""Database abstraction layer for MySQL 8.4 LTS workloads.

Provides connection pooling with exponential backoff retries, connect/read timeouts,
safe context managers, strictly parameterized execution, server telemetry helpers,
and strongly typed database exceptions.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import mysql.connector
from mysql.connector import errorcode, pooling
from mysql.connector.connection import MySQLConnection
from mysql.connector.cursor import MySQLCursor

from dbrescue.config import MySQLConnectionConfig, RescueException
from dbrescue.security import sanitize_error_message, validate_sql_identifier

logger = logging.getLogger("dbrescue.db")


# --- Typed Database Exceptions ---

class DatabaseError(RescueException):
    """Base exception for all database operational and connection failures."""
    pass


class DatabaseUnavailable(DatabaseError):
    """Raised when MySQL daemon is unreachable, socket is closed, or host refused connection."""
    pass


class DatabaseConnectionError(DatabaseError):
    """Raised when establishing a connection fails after exhaustion of retries."""
    pass


class QueryTimeout(DatabaseError):
    """Raised when query execution exceeds the configured read/execution timeout."""
    pass


class QueryExecutionError(DatabaseError):
    """Raised when SQL execution encounters a syntax error or constraint violation."""
    pass


class DatabaseAuthenticationError(DatabaseError):
    """Raised when database credentials (username/password) are rejected."""
    pass


class DatabaseNotFoundError(DatabaseError):
    """Raised when the specified target database does not exist on the server."""
    pass


# --- Dataclasses ---

@dataclass(frozen=True)
class QueryResult:
    """Encapsulates tabular query results along with execution metrics."""
    columns: list[str]
    rows: list[tuple[Any, ...]]
    row_count: int
    execution_time_ms: float

    def as_dict_list(self) -> list[dict[str, Any]]:
        """Converts tabular rows into a list of dictionaries mapping column name to value."""
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]


# --- Connection Pool & Factory ---

class MySQLConnectionPool:
    """Thread-safe connection pool wrapping mysql.connector.pooling.MySQLConnectionPool.

    Enforces connect timeouts, read timeouts, UTF8mb4 encoding, and automatic recycling.
    """

    def __init__(self, config: MySQLConnectionConfig, max_retries: int = 3, backoff_base_sec: float = 0.5) -> None:
        self.config = config
        self.max_retries = max_retries
        self.backoff_base_sec = backoff_base_sec
        self._pool: pooling.MySQLConnectionPool | None = None
        self._initialize_pool()

    def _initialize_pool(self) -> None:
        last_exception: Exception | None = None
        pool_name = f"dbrescue_pool_{self.config.host}_{self.config.port}_{id(self)}"

        for attempt in range(1, self.max_retries + 1):
            try:
                self._pool = pooling.MySQLConnectionPool(
                    pool_name=pool_name[:64],
                    pool_size=self.config.pool_size,
                    pool_reset_session=True,
                    host=self.config.host,
                    port=self.config.port,
                    user=self.config.user,
                    password=self.config.password,
                    database=self.config.database if self.config.database else None,
                    connection_timeout=self.config.connect_timeout_sec,
                    charset="utf8mb4",
                    collation="utf8mb4_0900_ai_ci",
                    use_pure=True,
                    autocommit=False,
                )
                logger.info(
                    "MySQL connection pool initialized successfully for %s:%d/%s (pool_size=%d)",
                    self.config.host,
                    self.config.port,
                    self.config.database,
                    self.config.pool_size,
                )
                return
            except mysql.connector.Error as err:
                last_exception = err
                sanitized_msg = sanitize_error_message(err)
                if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                    raise DatabaseAuthenticationError(f"Authentication failed: {sanitized_msg}") from err
                if err.errno == errorcode.ER_BAD_DB_ERROR:
                    raise DatabaseNotFoundError(f"Database not found: {sanitized_msg}") from err

                wait_sec = self.backoff_base_sec * (2 ** (attempt - 1))
                logger.warning(
                    "Connection attempt %d/%d failed to %s:%d: %s. Retrying in %.2fs...",
                    attempt,
                    self.max_retries,
                    self.config.host,
                    self.config.port,
                    sanitized_msg,
                    wait_sec,
                )
                time.sleep(wait_sec)

        raise DatabaseUnavailable(
            f"Unable to establish database connection after {self.max_retries} attempts: "
            f"{sanitize_error_message(str(last_exception))}"
        ) from last_exception

    @contextmanager
    def get_connection(self) -> Generator[MySQLConnection, None, None]:
        """Context manager that borrows a connection from the pool and returns it on exit."""
        if self._pool is None:
            self._initialize_pool()

        conn: MySQLConnection | None = None
        try:
            assert self._pool is not None
            conn = self._pool.get_connection()
            yield conn
        except mysql.connector.Error as err:
            sanitized = sanitize_error_message(err)
            if err.errno in (errorcode.CR_CONN_HOST_ERROR, errorcode.CR_CONNECTION_ERROR):
                raise DatabaseUnavailable(f"Database connection lost: {sanitized}") from err
            raise DatabaseConnectionError(f"Error obtaining connection from pool: {sanitized}") from err
        finally:
            if conn and conn.is_connected():
                conn.close()

    def close(self) -> None:
        """Closes all idle pooled connections."""
        self._pool = None


def create_connection_pool(
    config: MySQLConnectionConfig,
    max_retries: int = 3,
    backoff_base_sec: float = 0.5
) -> MySQLConnectionPool:
    """Factory creating an authenticated, pooled MySQL connector with retry backoff."""
    return MySQLConnectionPool(config, max_retries=max_retries, backoff_base_sec=backoff_base_sec)


# --- Execution Context Managers ---

@contextmanager
def transaction(conn: MySQLConnection) -> Iterator[MySQLCursor]:
    """Context manager providing an atomic transactional scope with automatic commit/rollback."""
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception as err:
        conn.rollback()
        sanitized = sanitize_error_message(err)
        logger.error("Transaction failed and was rolled back: %s", sanitized)
        raise
    finally:
        cursor.close()


# --- Parameterized Query Execution ---

def execute_query(
    pool_or_conn: MySQLConnectionPool | MySQLConnection,
    sql: str,
    params: Sequence[Any] | None = None,
    timeout_sec: int | None = None,
) -> QueryResult:
    """Executes a strictly parameterized SQL statement and returns tabular QueryResult.

    Raises:
        QueryExecutionError: If query execution fails or raises a syntax/constraint error.
        QueryTimeout: If query exceeds timeout.
    """
    start_time = time.perf_counter()

    def _execute_on_conn(conn: MySQLConnection) -> QueryResult:
        cursor = conn.cursor()
        try:
            if timeout_sec is not None and timeout_sec > 0:
                cursor.execute(f"SET SESSION max_execution_time = {int(timeout_sec * 1000)}")
            cursor.execute(sql, params or ())
            rows = cursor.fetchall() if cursor.description else []
            columns = [col[0] for col in cursor.description] if cursor.description else []
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=elapsed_ms,
            )
        except mysql.connector.Error as err:
            sanitized = sanitize_error_message(err)
            if "timeout" in str(err).lower() or err.errno == 1969:
                raise QueryTimeout(f"Query timed out: {sanitized}") from err
            raise QueryExecutionError(f"Query execution failed: {sanitized}") from err
        finally:
            cursor.close()

    if isinstance(pool_or_conn, MySQLConnectionPool):
        with pool_or_conn.get_connection() as c:
            return _execute_on_conn(c)
    else:
        return _execute_on_conn(pool_or_conn)


# --- Health & Telemetry Helpers ---

def ping_database(config: MySQLConnectionConfig, timeout_sec: int = 5) -> bool:
    """Tests TCP socket connection and authentication against a target MySQL instance."""
    try:
        conn = mysql.connector.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database if config.database else None,
            connection_timeout=timeout_sec,
            charset="utf8mb4",
            use_pure=True,
        )
        if conn.is_connected():
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchall()
            cursor.close()
            conn.close()
            return True
        return False
    except mysql.connector.Error as err:
        logger.debug("Database ping failed to %s:%d: %s", config.host, config.port, sanitize_error_message(err))
        return False


def get_server_version(pool_or_conn: MySQLConnectionPool | MySQLConnection) -> str:
    """Returns the MySQL server version string (e.g. '8.4.3')."""
    res = execute_query(pool_or_conn, "SELECT VERSION()")
    return str(res.rows[0][0]) if res.rows else "UNKNOWN"


def get_uptime(pool_or_conn: MySQLConnectionPool | MySQLConnection) -> int:
    """Returns the MySQL server uptime in seconds."""
    res = execute_query(pool_or_conn, "SHOW GLOBAL STATUS LIKE 'Uptime'")
    return int(res.rows[0][1]) if res.rows else 0


def get_threads_connected(pool_or_conn: MySQLConnectionPool | MySQLConnection) -> int:
    """Returns the number of currently active client connections."""
    res = execute_query(pool_or_conn, "SHOW GLOBAL STATUS LIKE 'Threads_connected'")
    return int(res.rows[0][1]) if res.rows else 0


def get_table_names(pool_or_conn: MySQLConnectionPool | MySQLConnection, database_name: str) -> list[str]:
    """Retrieves all user table names in the specified database, sorted alphabetically."""
    validated_db = validate_sql_identifier(database_name, "database_name")
    sql = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        ORDER BY table_name ASC;
    """
    res = execute_query(pool_or_conn, sql, (validated_db,))
    return [str(row[0]) for row in res.rows]


def get_table_row_count(
    pool_or_conn: MySQLConnectionPool | MySQLConnection,
    database_name: str,
    table_name: str,
    where_clause: str | None = None,
    params: Sequence[Any] | None = None,
) -> int:
    """Executes a parameterized COUNT(*) query on the specified database and table.

    Validates identifiers strictly against SQL injection attacks.
    """
    valid_db = validate_sql_identifier(database_name, "database_name")
    valid_tbl = validate_sql_identifier(table_name, "table_name")

    if where_clause:
        # Note: where_clause uses placeholders (%s), parameters passed in params
        sql = f"SELECT COUNT(*) FROM `{valid_db}`.`{valid_tbl}` WHERE {where_clause}"
        res = execute_query(pool_or_conn, sql, params or ())
    else:
        sql = f"SELECT COUNT(*) FROM `{valid_db}`.`{valid_tbl}`"
        res = execute_query(pool_or_conn, sql)

    return int(res.rows[0][0]) if res.rows else 0


def fetch_ordered_rows(
    pool_or_conn: MySQLConnectionPool | MySQLConnection,
    database_name: str,
    table_name: str,
    order_by_columns: Sequence[str],
    columns: Sequence[str] | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> QueryResult:
    """Fetches rows with guaranteed stable ordering by primary keys or specified columns."""
    valid_db = validate_sql_identifier(database_name, "database_name")
    valid_tbl = validate_sql_identifier(table_name, "table_name")

    # Validate all column identifiers
    for col in order_by_columns:
        validate_sql_identifier(col, "order_by_column")

    order_clause = ", ".join(f"`{col}` ASC" for col in order_by_columns)

    if columns:
        for col in columns:
            validate_sql_identifier(col, "column")
        select_clause = ", ".join(f"`{col}`" for col in columns)
    else:
        select_clause = "*"

    sql = f"SELECT {select_clause} FROM `{valid_db}`.`{valid_tbl}` ORDER BY {order_clause}"
    params: list[Any] = []

    if limit is not None:
        if limit < 0:
            raise QueryExecutionError("LIMIT must be non-negative.")
        sql += " LIMIT %s"
        params.append(limit)
        if offset is not None:
            if offset < 0:
                raise QueryExecutionError("OFFSET must be non-negative.")
            sql += " OFFSET %s"
            params.append(offset)

    return execute_query(pool_or_conn, sql, tuple(params) if params else None)
