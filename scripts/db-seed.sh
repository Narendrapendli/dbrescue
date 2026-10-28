#!/usr/bin/env bash
# ==============================================================================
# Applies synthetic seed data into the running student_db MySQL container.
# SAFETY GUARD: Persistent storage volumes are strictly preserved and never deleted.
# ==============================================================================
set -euo pipefail

CONTAINER_NAME="${DOCKER_PRIMARY_CONTAINER:-dbrescue-mysql-primary}"
ROOT_PASS="${MYSQL_ROOT_PASSWORD:-RootRescue2026!Emergency}"
SEED_FILE="db/seed/seed_students.sql"

echo "==> [dbrescue] Seeding synthetic student data into '${CONTAINER_NAME}'..."

if [ ! -f "$SEED_FILE" ]; then
    echo "[ERROR] Seed file '${SEED_FILE}' not found."
    exit 1
fi

# Verify container is running
STATUS=$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "stopped")
if [ "$STATUS" != "running" ]; then
    echo "[ERROR] Container '${CONTAINER_NAME}' is not running (status: ${STATUS}). Run ./scripts/db-up.sh first."
    exit 1
fi

# Ingest seed file
echo "--> Applying '${SEED_FILE}' via mysql CLI..."
docker exec -i "$CONTAINER_NAME" mysql -u root -p"$ROOT_PASS" < "$SEED_FILE"

# Verify counts
echo "--> Verifying seeded counts..."
QUERY="SELECT 'students' AS tbl, COUNT(*) AS count FROM student_db.students UNION ALL SELECT 'courses', COUNT(*) FROM student_db.courses UNION ALL SELECT 'enrollments', COUNT(*) FROM student_db.enrollments UNION ALL SELECT 'test_records', COUNT(*) FROM student_db.test_records;"
docker exec "$CONTAINER_NAME" mysql -u root -p"$ROOT_PASS" -e "$QUERY"

echo "==> [dbrescue] Database seeding completed successfully!"
