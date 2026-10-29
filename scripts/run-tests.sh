#!/usr/bin/env bash
# Run the automated pytest suite with coverage and JUnit XML report generation.
# Adheres strictly to the safety guard: never touches production mysql_data.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "=========================================================="
echo "  dbrescue Automated Acceptance Test Runner"
echo "=========================================================="

PYTHON_EXE="$PROJECT_ROOT/.venv/bin/python"
if [ ! -f "$PYTHON_EXE" ]; then
    PYTHON_EXE="python3"
fi

REPORTS_DIR="$PROJECT_ROOT/reports"
mkdir -p "$REPORTS_DIR"

JUNIT_XML="$REPORTS_DIR/junit.xml"
COV_JSON="$REPORTS_DIR/coverage.json"
COV_HTML="$REPORTS_DIR/coverage_html"

MARKER="${1:-}"
EXTRA_ARGS=()
if [ -n "$MARKER" ]; then
    EXTRA_ARGS+=("-m" "$MARKER")
fi

echo "Executing tests with coverage..."
"$PYTHON_EXE" -m pytest \
    "--junitxml=$JUNIT_XML" \
    "--cov=src" \
    "--cov-report=json:$COV_JSON" \
    "--cov-report=html:$COV_HTML" \
    "--cov-report=term-missing" \
    -v \
    "${EXTRA_ARGS[@]}"

EXIT_CODE=$?
echo "Test execution completed with exit code: $EXIT_CODE"
exit $EXIT_CODE
