-- ==============================================================================
-- dbrescue Dedicated Prometheus mysqld-exporter User (03_exporter_user.sql)
-- Least-privilege monitoring account per official Prometheus documentation.
-- ==============================================================================

CREATE USER IF NOT EXISTS 'exporter'@'%' IDENTIFIED WITH caching_sha2_password BY 'ExporterPass2026!Secure';

-- PROCESS: Allows mysqld-exporter to inspect thread states, locks, and query states
-- REPLICATION CLIENT: Allows monitoring replication status without altering state
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'exporter'@'%';

-- performance_schema: Allows querying table I/O, latency, and memory metrics
GRANT SELECT ON performance_schema.* TO 'exporter'@'%';

FLUSH PRIVILEGES;
