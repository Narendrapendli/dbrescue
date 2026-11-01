#!/usr/bin/env bash
# ==============================================================================
# dbrescue Production VM Provisioning & Deployment Script (deploy-ec2.sh)
# Automates complete host configuration on Amazon Linux 2023 or Ubuntu 24.04 LTS
# ==============================================================================
set -euxo pipefail

echo "=========================================================="
echo " Starting dbrescue Automated Production Host Deployment   "
echo "=========================================================="

# 1. Install prerequisites based on package manager
if command -v dnf &>/dev/null; then
    echo "--> Detected Amazon Linux / RHEL / Fedora environment..."
    sudo dnf update -y
    sudo dnf install -y docker git curl jq
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER" || true
elif command -v apt-get &>/dev/null; then
    echo "--> Detected Ubuntu / Debian environment..."
    sudo apt-get update -y
    sudo apt-get install -y docker.io git curl jq
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER" || true
else
    echo "Unsupported OS package manager. Please ensure Docker and Git are installed."
    exit 1
fi

# 2. Install Docker Compose v2 CLI plugin if missing
if ! docker compose version &>/dev/null; then
    echo "--> Installing Docker Compose v2 CLI plugin..."
    COMPOSE_DIR="${HOME}/.docker/cli-plugins"
    mkdir -p "$COMPOSE_DIR"
    ARCH=$(uname -m)
    curl -SL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-${ARCH}" -o "${COMPOSE_DIR}/docker-compose"
    chmod +x "${COMPOSE_DIR}/docker-compose"
fi

# 3. Create production .env configuration with secure generated passwords
echo "--> Configuring environment variables in .env..."
APP_PASSWORD=$(openssl rand -hex 16)
ROOT_PASSWORD=$(openssl rand -hex 20)
GRAFANA_PASSWORD=$(openssl rand -hex 16)

cat <<EOF > .env
DBRESCUE_ENV=production
DBRESCUE_LOG_LEVEL=INFO
DBRESCUE_SQLITE_PATH=./db/sqlite/operational.db
DBRESCUE_BACKUP_DIR=./backups
DBRESCUE_REPORTS_DIR=./docs/reports

MYSQL_PRIMARY_HOST=127.0.0.1
MYSQL_PRIMARY_PORT=3306
MYSQL_DATABASE=student_db
MYSQL_USER=rescue_app
MYSQL_PASSWORD=${APP_PASSWORD}
MYSQL_ROOT_PASSWORD=${ROOT_PASSWORD}

MYSQL_SANDBOX_HOST=127.0.0.1
MYSQL_SANDBOX_PORT=3307

DOCKER_PRIMARY_CONTAINER=dbrescue-mysql-primary
DOCKER_SANDBOX_CONTAINER=dbrescue-mysql-sandbox
DOCKER_TARGET_VOLUME=dbrescue_mysql_data

PROMETHEUS_METRICS_PORT=9108
STREAMLIT_SERVER_PORT=8501

BACKUP_RETENTION_DAYS=30
MAX_DATASET_SIZE_MB=10240
SANDBOX_TIMEOUT_SEC=60
MAX_ALLOWED_ROW_DISCREPANCY=0

GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
EOF

chmod 600 .env
echo "--> .env generated and secured with 0600 permissions."

# 4. Launch MySQL 8.4 LTS and Streamlit Application Containers
echo "--> Starting containers with Docker Compose..."
docker compose up -d mysql app

# 5. Wait for MySQL healthcheck
echo "--> Waiting for MySQL 8.4 LTS container to report healthy..."
until [ "$(docker inspect --format='{{json .State.Health.Status}}' dbrescue-mysql-primary 2>/dev/null)" = '"healthy"' ]; do
    echo "Waiting for MySQL socket initialization..."
    sleep 3
done

# 6. Apply relational seed data
echo "--> Seeding database fixtures..."
bash scripts/db-seed.sh

# 7. Start monitoring stack overlay
echo "--> Starting Prometheus, Grafana, and Exporters..."
docker compose -f compose.yaml -f compose.monitoring.yaml --profile monitoring up -d

echo "=========================================================="
echo " dbrescue Successfully Deployed!                          "
echo " Streamlit Dashboard: http://localhost:8501               "
echo " Grafana Dashboard:   http://localhost:3000               "
echo " Prometheus API:      http://localhost:9090               "
echo "=========================================================="
