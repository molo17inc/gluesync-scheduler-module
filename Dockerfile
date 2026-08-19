# Build stage
# Chainguard/Wolfi Python (-dev variant): glibc-based, continuously patched for
# near-zero CVEs, and ships pip + a shell + apk for building dependencies.
FROM cgr.dev/chainguard/python:latest-dev AS builder

USER root
WORKDIR /build

# Set environment variables for Python and dependency installation
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

# Native build toolchain for compiling any sdists (Wolfi/glibc package names)
RUN apk add --no-cache \
    build-base \
    gcc \
    glibc-dev \
    openssl-dev \
    libffi-dev \
    pkgconf \
    rust \
    patchelf

# Isolated virtual environment that is copied verbatim into the final stage
RUN python -m venv /opt/venv && \
    python -m pip install --upgrade pip wheel setuptools

# Install application dependencies into the venv
COPY ./requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
    --extra-index-url "https://oauth2:${GITLAB_TOKEN}@gitlab.com/api/v4/projects/68232363/packages/pypi/simple" \
    --prefer-binary \
    --only-binary=cryptography,pyjks,websockets \
    -r requirements.txt

# Build and install the application package into the venv
COPY ./setup.py ./
COPY ./pyproject.toml ./
COPY ./VERSION ./
COPY ./README.md ./
COPY ./gluesync_scheduler ./gluesync_scheduler
RUN pip install .

# Final stage
# Same Chainguard/Wolfi Python image: keeps a POSIX shell so entrypoint.sh and
# the migration script still run, while dropping the Debian OS packages (perl,
# curl, ncurses, sqlite, libssh2, ...) that carried unfixable CVEs.
FROM cgr.dev/chainguard/python:latest-dev

USER root
WORKDIR /app

# Minimal runtime OS packages (Wolfi, continuously patched).
# latest-dev still ships setuptools 70.3.0 + msgpack 1.1.2 (HIGH); Wolfi
# already has the fixed packages, but the public image has not rebuilt yet.
RUN apk add --no-cache openssl procps tzdata py3-setuptools \
    && python -m pip install --upgrade --force-reinstall --no-cache-dir \
        'setuptools>=78.1.1' 'msgpack>=1.2.1'

# Bring in the prebuilt virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Create Gluesync default directories and app data directory
RUN mkdir -p /opt/gluesync/data && \
    mkdir -p /opt/gluesync/shared && \
    mkdir -p /app/data && \
    mkdir -p /app/logs && \
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
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH" \
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

# Copy VERSION file to the final image
COPY --from=builder /build/VERSION /app/

# Copy application source
COPY ./setup.py .
COPY ./entrypoint.sh .
COPY ./README.md .
COPY ./gluesync_scheduler ./gluesync_scheduler
COPY ./migrations ./migrations

# Copy remaining app code
COPY . .

# Make scripts executable
RUN chmod +x /app/entrypoint.sh /app/docker-entrypoint.sh /app/migrations/run_migrations.sh

# Expose the port the app runs on
EXPOSE 1717

# Set entrypoint to ensure cron service starts
ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
# Use python3 directly to ensure our SSL extraction code runs
CMD ["python3", "run_scheduler.py"]
