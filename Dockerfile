# Build stage for SDK installation
# Use Python 3.13 slim-trixie (Debian 13) for latest security fixes
FROM python:3.13-slim-trixie AS builder

# Non-root runtime user/group ids (overridable at build time)
ARG USER_UID=10001
ARG USER_GID=10001

WORKDIR /build

# Install build dependencies (single RUN) with security updates from trixie
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
    apt-get upgrade -y && \
    apt-get dist-upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set environment variables for Python and dependency installation
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build dependencies already installed above

# Copy requirements and prebuild wheels for all Python deps
COPY --chown=$USER_UID:$USER_GID ./requirements.txt ./requirements.txt

# Create wheels directory
RUN mkdir -p /wheels

# Upgrade pip tooling to latest version for ARM wheel compatibility
RUN python -m pip install --upgrade pip wheel setuptools

# Build wheels for application requirements (prioritize ARM wheels)
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip wheel --wheel-dir=/wheels \
    --extra-index-url "https://oauth2:${GITLAB_TOKEN}@gitlab.com/api/v4/projects/68232363/packages/pypi/simple" \
    --prefer-binary \
    --only-binary=cryptography,pyjks,websockets \
    -r requirements.txt

# SDK will be installed from GitLab PyPI registry via requirements.txt

# Copy project files and build the application wheel (prepackaged for offline install)
COPY --chmod=644 ./setup.py ./
COPY --chmod=644 ./pyproject.toml ./
COPY --chmod=644 ./VERSION ./
COPY ./README.md ./
COPY ./gluesync_scheduler ./gluesync_scheduler
RUN python -m pip wheel --wheel-dir=/wheels .

# Final stage
# Use Python 3.13 slim-trixie (Debian 13) for latest security fixes
FROM python:3.13-slim-trixie

# Non-root runtime user/group ids (overridable at build time)
ARG USER_UID=10001
ARG USER_GID=10001

# Create a dedicated non-root user/group to run the application
RUN groupadd --gid "$USER_GID" gluesync && \
    useradd --uid "$USER_UID" --gid "$USER_GID" --create-home --shell /usr/sbin/nologin gluesync

WORKDIR /app

# Install required system packages - trixie has fixed versions of vulnerable packages
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    openssl && \
    apt-get upgrade -y && \
    apt-get dist-upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create Gluesync default directories and app data directory
RUN mkdir -p /opt/gluesync/data && \
    mkdir -p /opt/gluesync/shared && \
    mkdir -p /app/data && \
    mkdir -p /app/logs && \
    chown -R $USER_UID:$USER_GID /opt/gluesync /app && \
    chmod -R 755 /opt/gluesync && \
    chmod -R 750 /app/data && \
    chmod -R 750 /app/logs

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
    GLUESYNC_LICENSE_FILE=/opt/gluesync/shared/gs-license.dat \
    GLUESYNC_SECURITY_CONFIG=/opt/gluesync/shared/security-config.json \
    GLUESYNC_HOST= \
    DB_URL=sqlite:///./data/scheduler.db \
    ENTITY_START_TIMEOUT=2 \
    HOST=0.0.0.0 \
    PORT=1717 \
    DEBUG=false \
    LOG_LEVEL=INFO \
    LOG_DIR=/app/logs \
    DATA_DIR=/app/data \
    ALLOWED_ORIGINS=* \
    CRONTAB_USER=root \
    SSL_ENABLED=false \
    SSL_SKIP_VERIFY=true \
    TIMEZONE=UTC \
    FIRE_ONCE=false

# (combined into single apt install above)

# Copy built wheels from builder stage
COPY --from=builder /wheels /wheels

# Copy VERSION file to the final image
COPY --from=builder /build/VERSION /app/
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

# Make scripts executable and ensure the non-root user owns the application
# and Gluesync data directories, then drop privileges so the container does
# not run as root.
RUN chmod +x /app/entrypoint.sh /app/docker-entrypoint.sh /app/migrations/run_migrations.sh && \
    chown -R "$USER_UID:$USER_GID" /app /opt/gluesync
USER gluesync

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
