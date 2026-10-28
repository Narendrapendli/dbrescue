#!/usr/bin/env bash
# ==============================================================================
# Bootstrap script for dbrescue development and runtime environment (Bash)
# ==============================================================================
set -euo pipefail

echo "==> [dbrescue] Initializing environment bootstrap..."

# 1. Determine Python command
PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" &> /dev/null; then
    PYTHON_BIN="python"
fi

echo "--> Using Python interpreter: $($PYTHON_BIN --version)"

# 2. Create virtual environment
if [ ! -d ".venv" ]; then
    echo "--> Creating virtual environment in .venv..."
    "$PYTHON_BIN" -m venv .venv
else
    echo "--> Existing .venv directory found."
fi

VENV_PYTHON="./.venv/bin/python"
VENV_PIP="./.venv/bin/pip"

# 3. Upgrade pip and install pinned dependencies
echo "--> Installing pinned dependencies from requirements-dev.txt..."
"$VENV_PIP" install --upgrade pip
"$VENV_PIP" install -r requirements-dev.txt

# 4. Copy .env.example to .env if missing
if [ ! -f ".env" ]; then
    echo "--> Initializing .env from .env.example..."
    cp .env.example .env
    chmod 600 .env
    echo "--> IMPORTANT: Edit .env to set your secure database passwords."
else
    echo "--> .env already exists. Preserving existing configuration."
fi

# 5. Create required directories
mkdir -p backups logs docs/reports docs/evidence db/sqlite

echo "==> [dbrescue] Environment bootstrap completed successfully!"
echo "    Activate your environment using: source .venv/bin/activate"
