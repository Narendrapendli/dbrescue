#!/usr/bin/env bash
# ==============================================================================
# Reports status of dbrescue MySQL containers, healthchecks, and table counts.
# SAFETY GUARD: Persistent storage volumes are strictly preserved and never deleted.
# ==============================================================================
set -u

CONTAINER_NAME="${DOCKER_PRIMARY_CONTAINER:-dbrescue-mysql-primary}"
ROOT_PASS="${MYSQL_ROOT_PASSWORD:-RootRescue2026!Emergency}"

echo "=================================================================="
echo "                dbrescue Database Status Report                   "
echo "=================================================================="

# Check Docker CLI
if ! command -v docker &> /dev/null; then
    echo "[STATUS] Docker CLI is not installed or not in PATH."
    exit 0
fi

# Inspect container
STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "not_found")
if [ "$STATUS" = "not_found" ]; then
    echo "[CONTAINER] Container '${CONTAINER_NAME}' does NOT exist."
    exit 0
fi

HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}N/A{{end}}' "$CONTAINER_NAME" 2>/dev/null || echo "N/A")
echo "[CONTAINER] Name    : ${CONTAINER_NAME}"
echo "[CONTAINER] Status  : ${STATUS}"
echo "[CONTAINER] Health  : ${HEALTH}"

# Port bindings
PORTS=$(docker port "$CONTAINER_NAME" 3306 2>/dev/null || echo "N/A")
echo "[NETWORK]   Port 3306 -> ${PORTS}"

# Volume mounts
echo "[STORAGE]   Mounts:"
docker inspect --format '{{range .Mounts}}{{.Type}}: {{.Source}} -> {{.Destination}} (Mode: {{.Mode}}){{"\n"}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || echo "None"

# Query row counts if running
if [ "$STATUS" = "running" ]; then
    echo "[DATA]      Checking table row counts in student_db..."
    QUERY="SELECT 'students' AS tbl, COUNT(*) AS count FROM student_db.students UNION ALL SELECT 'courses', COUNT(*) FROM student_db.courses UNION ALL SELECT 'enrollments', COUNT(*) FROM student_db.enrollments UNION ALL SELECT 'test_records', COUNT(*) FROM student_db.test_records;"
    docker exec "$CONTAINER_NAME" mysql -u root -p"$ROOT_PASS" -e "$QUERY" 2>/dev/null || echo "Query failed."
fi

echo "=================================================================="
