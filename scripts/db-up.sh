#!/usr/bin/env bash
# ==============================================================================
# Starts dbrescue MySQL 8.4 LTS service and waits for healthy status.
# SAFETY GUARD: Persistent storage volumes are strictly preserved and never deleted.
# ==============================================================================
set -euo pipefail

TIMEOUT_SEC=60
CONTAINER_NAME="${DOCKER_PRIMARY_CONTAINER:-dbrescue-mysql-primary}"

echo "==> [dbrescue] Starting infrastructure via Docker Compose..."

# 1. Verify Docker CLI
if ! command -v docker &> /dev/null; then
    echo "[ERROR] 'docker' command not found in PATH."
    echo "        Please install and start Docker: https://docs.docker.com/engine/install/"
    exit 1
fi

# 2. Check daemon
if ! docker info &> /dev/null; then
    echo "[ERROR] Docker daemon is not running or unreachable."
    echo "        Please start Docker daemon and retry."
    exit 1
fi

# 3. Launch MySQL service
echo "--> Running 'docker compose up -d mysql'..."
docker compose up -d mysql

# 4. Wait for healthy
echo "--> Awaiting healthcheck for container '${CONTAINER_NAME}' (timeout: ${TIMEOUT_SEC}s)..."
ELAPSED=0
INTERVAL=2

while [ "$ELAPSED" -lt "$TIMEOUT_SEC" ]; do
    STATUS=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || echo "not_found")
    
    if [ "$STATUS" = "healthy" ]; then
        echo "--> Container '${CONTAINER_NAME}' is HEALTHY!"
        echo "--> Port mapping: $(docker port "$CONTAINER_NAME" 3306 2>/dev/null)"
        exit 0
    fi
    
    if [ "$STATUS" = "unhealthy" ]; then
        echo "[ERROR] Container '${CONTAINER_NAME}' reported UNHEALTHY."
        docker logs --tail 30 "$CONTAINER_NAME"
        exit 1
    fi
    
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))
    echo "    [${ELAPSED}s] Health status: ${STATUS}..."
done

echo "[ERROR] Timed out waiting for '${CONTAINER_NAME}' after ${TIMEOUT_SEC} seconds."
docker logs --tail 30 "$CONTAINER_NAME"
exit 1
