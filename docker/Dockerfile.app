# ==============================================================================
# Streamlit Web Application Container Definition (dbrescue)
# Non-root user, multi-stage caching, security-hardened
# ==============================================================================

FROM python:3.12-slim-bookworm

# Prevent Python from writing bytecode and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install system dependencies including curl for container healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy and install pinned requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Create dedicated non-root application user
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

# Copy application source code and configurations
COPY src/ ./src/
COPY app/ ./app/
COPY docs/ ./docs/

# Create runtime directories with appropriate non-root ownership
RUN mkdir -p /app/backups /app/docs/reports /app/logs /app/db/sqlite && \
    chown -R appuser:appgroup /app

# Switch to unprivileged user
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["streamlit", "run", "app/main.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
