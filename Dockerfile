# Base image and Python version (shared by both stages)
# The floating cgr.dev/chainguard/python:latest-dev tag jumped to Python 3.15 in
# Oct 2026, so prebuilt wheels stopped matching and pydantic-core was compiled
# from Rust on every build (50+ min under QEMU arm64). The free Chainguard tier
# only publishes latest/latest-dev for python, so we start from Wolfi base
# pinned by digest and install a fixed Python minor from Wolfi packages.
# Builder and runtime MUST use the same PYTHON_VERSION: the venv is copied as is.
# To update: bump the digest (crane digest cgr.dev/chainguard/wolfi-base:latest)
# and/or PYTHON_VERSION, after checking every requirement has manylinux wheels
# for both amd64 and arm64 on that Python version.
ARG BASE_IMAGE=cgr.dev/chainguard/wolfi-base:latest@sha256:c2a4815b6ca49b9bbafd451b4bde690d567a86b2b6b54877faac8082a7bc4296
ARG PYTHON_VERSION=3.13

# Build stage
# Wolfi (glibc-based, continuously patched for near-zero CVEs) with a shell + apk.
FROM ${BASE_IMAGE} AS builder
ARG PYTHON_VERSION
# Optional token for the private GitLab PyPI index. Pass with
# --build-arg GITLAB_TOKEN=...; when unset the index is accessed anonymously.
ARG GITLAB_TOKEN=""

USER root
WORKDIR /build

# Set environment variables for Python and dependency installation
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

# Fixed Python minor plus the native toolchain for the few sdist-only deps
# (twofish via pyjks). Rust is no longer needed: pydantic-core ships wheels.
RUN apk add --no-cache \
    python-${PYTHON_VERSION} \
    python-${PYTHON_VERSION}-dev \
    py${PYTHON_VERSION}-pip \
    build-base \
    gcc \
    glibc-dev \
    openssl-dev \
    libffi-dev \
    pkgconf \
    patchelf

# Isolated virtual environment that is copied verbatim into the final stage
RUN python${PYTHON_VERSION} -m venv /opt/venv && \
    python -m pip install --upgrade pip wheel setuptools

# Install application dependencies into the venv
COPY ./requirements.txt ./requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
    --extra-index-url "https://oauth2:${GITLAB_TOKEN}@gitlab.com/api/v4/projects/68232363/packages/pypi/simple" \
    --prefer-binary \
    --only-binary=cryptography,pyjks,websockets,pydantic-core \
    -r requirements.txt

# Build and install the application package into the venv
COPY ./setup.py ./
COPY ./pyproject.toml ./
COPY ./VERSION ./
COPY ./README.md ./
COPY ./gluesync_scheduler ./gluesync_scheduler
RUN pip install .

# Drop pip from the runtime venv: nothing installs packages at runtime, and pip
# vendors a setuptools 70.3.0 SBOM (pip/_vendor/bom.cdx.json) that scanners
# report as CVE-2025-47273 even though the installed setuptools is current.
RUN /opt/venv/bin/python -m pip uninstall -y pip

# Final stage
# Same pinned Wolfi base and Python minor as the builder: keeps a POSIX shell so
# entrypoint.sh and the migration script still run, while dropping the Debian OS
# packages (perl, curl, ncurses, sqlite, libssh2, ...) that carried unfixable CVEs.
FROM ${BASE_IMAGE}
ARG PYTHON_VERSION

USER root
WORKDIR /app

# Minimal runtime OS packages (Wolfi, continuously patched). Only the Python
# interpreter is installed system-wide (no system pip/setuptools/msgpack), so the
# old latest-dev setuptools/msgpack CVE workaround is no longer needed; the venv
# copied below carries an up-to-date pip/setuptools from the builder.
RUN apk add --no-cache python-${PYTHON_VERSION} bash openssl procps tzdata

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
