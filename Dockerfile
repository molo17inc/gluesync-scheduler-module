# Build stage for SDK installation
FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies (single RUN)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libc6-dev \
    python3-dev \
    libssl-dev \
    libffi-dev \
    pkg-config \
    rustc \
    cargo \
    patchelf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set environment variables for Python and dependency installation
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build dependencies already installed above

# Copy requirements and prebuild wheels for all Python deps
COPY ./requirements.txt ./requirements.txt

# Create wheels directory
RUN mkdir -p /wheels

# Upgrade pip tooling
RUN python -m pip install --upgrade pip wheel setuptools

# Build wheels for application requirements (downloads manylinux wheels when available)
RUN python -m pip wheel --wheel-dir=/wheels -r requirements.txt

# SDK will be installed from GitLab PyPI registry via requirements.txt

# Copy project files and build the application wheel (prepackaged for offline install)
COPY ./setup.py ./
COPY ./pyproject.toml ./
COPY ./README.md ./
COPY ./gluesync_scheduler ./gluesync_scheduler
RUN python -m pip wheel --wheel-dir=/wheels .

# Final stage
FROM python:3.13-slim

WORKDIR /app

# Install required system packages in a single layer (minimal)
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    openssl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create Gluesync default directories and app data directory
RUN mkdir -p /opt/gluesync/data && \
    mkdir -p /app/data && \
    mkdir -p /app/logs && \
    chmod -R 777 /app/data && \
    chmod -R 777 /app/logs

# Set environment variables
# Core Hub settings
# API Server settings
# Database settings
# CORS settings
# Scheduler settings
# Gluesync SDK settings using default paths
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    CORE_HUB_URL= \
    ENTITY_START_TIMEOUT=2 \
    HOST=0.0.0.0 \
    PORT=1717 \
    DEBUG=False \
    LOG_LEVEL=INFO \
    DATA_DIR=/app/data \
    DB_URL=sqlite:///./data/scheduler.db \
    ALLOWED_ORIGINS=* \
    CRONTAB_USER=root \
    GLUESYNC_LICENSE_FILE=/opt/gluesync/data/gs-license.dat \
    SSL_ENABLED=False \
    GLUESYNC_SECURITY_CONFIG=/opt/gluesync/data/security-config.json \
    SSL_SKIP_VERIFY=True \
    TIMEZONE=UTC

# (combined into single apt install above)

# Copy built wheels from builder stage
COPY --from=builder /wheels /wheels

# Copy requirements and setup files
COPY ./requirements.txt .
# Install Python dependencies from prebuilt wheels (no compiler/runtime build deps needed)
RUN python -m pip install --no-index --find-links=/wheels -r requirements.txt && \
    python -m pip install --no-index --find-links=/wheels gluesync-scheduler-module && \
    rm -rf /wheels
COPY ./setup.py .
COPY ./entrypoint.sh .
COPY ./README.md .

# Copy the restructured package
COPY ./gluesync_scheduler ./gluesync_scheduler

# Copy migrations directory
COPY ./migrations ./migrations

# Copy app code
COPY . .

# Make scripts executable
RUN chmod +x /app/entrypoint.sh /app/docker-entrypoint.sh /app/migrations/run_migrations.sh

# Python dependencies installed from requirements.txt above

# (build-essential and libssl-dev installed above)

# SDK installed from wheels; no manual site-packages copy needed

# (Python dependencies were installed earlier for better caching)

# Expose the port the app runs on
EXPOSE 1717

# Set entrypoint to ensure cron service starts
ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
# Use python3 directly to ensure our SSL extraction code runs
CMD ["python3", "run_scheduler.py"]
