# dbrescue Observability & Telemetry Infrastructure Guide

> **Document Version:** 1.0.0  
> **Target Platform:** MySQL 8.4 LTS, Prometheus v2.55.1, Grafana 11.3.0, mysqld-exporter v0.16.0, cAdvisor v0.49.1  
> **Security Guard:** All exposed monitoring ports bind strictly to `127.0.0.1` (never `0.0.0.0`).

---

## 1. Architecture Overview

The `dbrescue` monitoring stack provides enterprise-grade observability across database engine internals, Docker container resource utilization, and disaster recovery SLA metrics.

```
+-----------------------------------------------------------------------------------------+
|                                    dbrescue Network                                     |
|                                                                                         |
|  +--------------------+       +----------------------+       +-----------------------+  |
|  | MySQL 8.4 LTS      | <==== | mysqld-exporter      | <==== | Prometheus v2.55.1    |  |
|  | (127.0.0.1:3306)   |       | (127.0.0.1:9104)     |       | (127.0.0.1:9090)      |  |
|  +--------------------+       +----------------------+       +-----------+-----------+  |
|                                                                          |              |
|  +--------------------+                                                  |              |
|  | Docker Host Socket | <==== | cAdvisor             | <=================+              |
|  | (/var/run/docker)  |       | (127.0.0.1:8080)     |                   |              |
|  +--------------------+       +----------------------+                   |              |
|                                                                          |              |
|  +--------------------+                                                  |              |
|  | dbrescue Python    | <================================================+              |
|  | (127.0.0.1:9108)   |                                                  |              |
|  +--------------------+                                                  v              |
|                                                              +-----------------------+  |
|                                                              | Grafana 11.3.0        |  |
|                                                              | (127.0.0.1:3000)      |  |
|                                                              +-----------------------+  |
+-----------------------------------------------------------------------------------------+
```

---

## 2. Service Endpoints & Port Mappings

All services strictly bind to loopback (`127.0.0.1`):

| Service | Container Name | Host Port | Protocol | Purpose | Authentication / Creds |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **Prometheus** | `dbrescue-prometheus` | `127.0.0.1:9090` | HTTP | Time-series database & alert manager | None (Loopback bound) |
| **Grafana** | `dbrescue-grafana` | `127.0.0.1:3000` | HTTP | Visual dashboard control plane | `admin` / `${GRAFANA_ADMIN_PASSWORD}` |
| **mysqld-exporter**| `dbrescue-mysqld-exporter` | `127.0.0.1:9104` | HTTP | MySQL engine metrics exporter | MySQL user `exporter` |
| **cAdvisor** | `dbrescue-cadvisor` | `127.0.0.1:8080` | HTTP | Container resource utilization | None (Loopback bound) |
| **dbrescue Exporter** | `dbrescue-app` | `127.0.0.1:9108` | HTTP | DR and integrity metrics endpoint | None (Loopback bound) |

---

## 3. Quickstart & Startup Commands

### Starting the Core Database & Monitoring Stack

```bash
# 1. Start core database infrastructure
docker compose -f compose.yaml up -d mysql

# 2. Launch the monitoring stack overlay with the 'monitoring' profile
docker compose -f compose.yaml -f compose.monitoring.yaml --profile monitoring up -d
```

### PowerShell (Windows)
```powershell
# 1. Start MySQL
docker compose -f compose.yaml up -d mysql

# 2. Launch Monitoring Stack
docker compose -f compose.yaml -f compose.monitoring.yaml --profile monitoring up -d
```

### Stopping the Monitoring Stack (Preserving All Volumes)
```bash
# Safe stop (NEVER with -v)
docker compose -f compose.yaml -f compose.monitoring.yaml --profile monitoring down
```

---

## 4. Key Metrics Scraped

### 4.1 MySQL Metrics (`mysqld-exporter`)
- `mysql_up`: `1` if MySQL is reachable; `0` if down.
- `mysql_global_status_threads_connected`: Current active client connections.
- `mysql_global_status_threads_running`: Number of currently executing queries.
- `rate(mysql_global_status_queries[1m])`: Query throughput (QPS).
- `mysql_global_status_innodb_buffer_pool_bytes_data`: InnoDB data cache allocation.

### 4.2 Docker Container Metrics (`cAdvisor`)
- `container_cpu_usage_seconds_total`: Cumulative CPU time per container.
- `container_memory_working_set_bytes`: Working set memory (RSS) consumed per container.
- `container_start_time_seconds`: Uptime tracking and restart detection.

### 4.3 Disaster Recovery Metrics (`dbrescue_*`)
- `dbrescue_last_test_status`: Status of last 18-stage disaster recovery run (`1` = PASSED, `0` = FAILED).
- `dbrescue_last_test_duration_seconds`: Total recovery duration (Recovery Time Objective / RTO).
- `dbrescue_last_backup_age_seconds`: Elapsed time since newest verified backup (Recovery Point Objective / RPO).
- `dbrescue_record_count_baseline`: Table row count at snapshot time.
- `dbrescue_record_count_recovered`: Table row count after container recreation.
- `dbrescue_metrics_freshness_seconds`: Elapsed time since the last telemetry collection.

---

## 5. Alert Rules Reference (`monitoring/alert_rules.yml`)

The following alerts are actively evaluated by Prometheus:

1. **`MySQLDown`** (Critical): Triggers when `mysql_up == 0` for > 30 seconds.
2. **`MySQLHighConnections`** (Warning): Triggers when `threads_connected > 80` for > 1 minute.
3. **`MySQLContainerRestart`** (Warning): Triggers when container uptime is under 120 seconds.
4. **`DBRescuePersistenceTestFailed`** (Critical): Triggers when `dbrescue_last_test_status == 0` (test failed).
5. **`DBRescueBackupOverdue`** (Warning): Triggers when newest backup is older than 86,400s (24h).
6. **`DBRescueRecordCountMismatch`** (Critical): Triggers when `baseline != recovered` row counts.
7. **`DBRescueMetricsStale`** (Warning): Triggers when telemetry is older than 300s.

---

## 6. Grafana Provisioning & Dashboards

Grafana is pre-configured for zero-touch provisioning:
- **Datasource**: Auto-configured Prometheus instance pointing to `http://prometheus:9090` via `monitoring/grafana/provisioning/datasources/prometheus.yml`.
- **Dashboard**: Auto-imported dashboard `dbrescue Disaster Recovery & Observability` via `monitoring/grafana/provisioning/dashboards/dashboards.yml` and `monitoring/grafana/dashboards/dbrescue_overview.json`.

---

## 7. Evidence & Screenshots to Capture

When running in an environment with Docker Desktop active:
1. **Prometheus Targets Page (`http://127.0.0.1:9090/targets`)**:
   - Verify `mysqld-exporter`, `cadvisor`, and `dbrescue-metrics` show state `UP`.
2. **Prometheus Alert Rules Page (`http://127.0.0.1:9090/alerts`)**:
   - Verify all 7 alert rules are registered and in `OK` state.
3. **Grafana Dashboard (`http://127.0.0.1:3000/d/dbrescue-dr-overview`)**:
   - Capture the overview showing MySQL health stat, threads connected, QPS graph, and disaster recovery scorecard panels.

---

## 8. Troubleshooting Guide

### Issue: `mysqld-exporter` cannot connect to MySQL
- **Symptom**: `mysqld-exporter` logs show `Access denied for user 'exporter'@'%'`.
- **Solution**: Confirm `03_exporter_user.sql` has run. Connect as root and run:
  ```sql
  CREATE USER IF NOT EXISTS 'exporter'@'%' IDENTIFIED WITH caching_sha2_password BY 'ExporterPass2026!Secure';
  GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'exporter'@'%';
  GRANT SELECT ON performance_schema.* TO 'exporter'@'%';
  FLUSH PRIVILEGES;
  ```

### Issue: Prometheus target shows `DOWN`
- **Symptom**: Prometheus target status displays `connection refused`.
- **Solution**: Check that the target container is on the `dbrescue_net` bridge network (`docker network inspect dbrescue_network`).

### Issue: Grafana admin password login fails
- **Symptom**: `Invalid username or password` on `http://127.0.0.1:3000`.
- **Solution**: Set `GRAFANA_ADMIN_PASSWORD` in your `.env` file before running compose, or use the default `GrafanaSecure2026!Pass`.
