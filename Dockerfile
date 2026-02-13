# Build stage for SDK installation
FROM python:3.13-slim AS builder

ARG USERNAME=gluesync
ARG USER_UID=1017
ARG USER_GID=$USER_UID

WORKDIR /build

# Create the user
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME

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
COPY --chown=$USER_UID:$USER_GID ./setup.py ./
COPY --chown=$USER_UID:$USER_GID ./pyproject.toml ./
COPY --chown=$USER_UID:$USER_GID ./VERSION ./
COPY --chown=$USER_UID:$USER_GID ./README.md ./
COPY --chown=$USER_UID:$USER_GID ./gluesync_scheduler ./gluesync_scheduler
RUN python -m pip wheel --wheel-dir=/wheels .

# Final stage
FROM python:3.13-slim

ARG USERNAME=gluesync
ARG USER_UID=1017
ARG USER_GID=$USER_UID

WORKDIR /app

# Install required system packages in a single layer (minimal)
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    openssl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create the user in final stage
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME

# Create Gluesync default directories and app data/log folders with proper ownership
RUN install -d -m 755 -o $USER_UID -g $USER_GID /opt/gluesync && \
    install -d -m 750 -o $USER_UID -g $USER_GID /opt/gluesync/data && \
    install -d -m 750 -o $USER_UID -g $USER_GID /opt/gluesync/data/chronos && \
    install -d -m 750 -o $USER_UID -g $USER_GID /opt/gluesync/shared && \
    install -d -m 750 -o $USER_UID -g $USER_GID /app/data && \
    install -d -m 750 -o $USER_UID -g $USER_GID /app/data/database && \
    install -d -m 750 -o $USER_UID -g $USER_GID /app/logs && \
    install -d -m 750 -o $USER_UID -g $USER_GID /app/logs/sdk && \
    install -d -m 750 -o $USER_UID -g $USER_GID /app/logs/cron

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
COPY --chown=$USER_UID:$USER_GID --from=builder /wheels /wheels

# Copy VERSION file to the final image
COPY --chown=$USER_UID:$USER_GID --from=builder /build/VERSION /app/
COPY --chown=$USER_UID:$USER_GID ./requirements.txt .
# Install Python dependencies from prebuilt wheels (no compiler/runtime build deps needed)
RUN python -m pip install --no-index --find-links=/wheels -r requirements.txt && \
    python -m pip install --no-index --find-links=/wheels gluesync-scheduler-module && \
    rm -rf /wheels
COPY --chown=$USER_UID:$USER_GID ./setup.py .
COPY --chown=$USER_UID:$USER_GID ./entrypoint.sh .
COPY --chown=$USER_UID:$USER_GID ./README.md .

# Copy the restructured package
COPY --chown=$USER_UID:$USER_GID ./gluesync_scheduler ./gluesync_scheduler

# Copy migrations directory
COPY --chown=$USER_UID:$USER_GID ./migrations ./migrations

# Copy app code
COPY --chown=$USER_UID:$USER_GID . .

# Make scripts executable
RUN chmod +x /app/entrypoint.sh /app/docker-entrypoint.sh /app/migrations/run_migrations.sh

# Set ownership of /app to gluesync user
RUN chown -R $USER_UID:$USER_GID /app /opt/gluesync

# Python dependencies installed from requirements.txt above

# (build-essential and libssl-dev installed above)

# SDK installed from wheels; no manual site-packages copy needed

# (Python dependencies were installed earlier for better caching)

# Switch to non-root user
USER $USERNAME

# Expose the port the app runs on
EXPOSE 1717

# Set entrypoint to ensure cron service starts
ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
# Use python3 directly to ensure our SSL extraction code runs
CMD ["python3", "run_scheduler.py"]
