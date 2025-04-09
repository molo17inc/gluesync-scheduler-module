# Build stage for SDK installation
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    python3-dev

# Set environment variables for Python and dependency installation
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    python3-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy only the SDK submodule
COPY ./gluesync-sdk ./gluesync-sdk

# Install specific websockets version first to avoid compatibility issues
RUN python -m pip install websockets==11.0.3

# Create wheels directory
RUN mkdir -p /wheels

# Install wheel and setuptools
RUN python -m pip install --upgrade pip wheel setuptools

# Install the SDK directly instead of trying to create a wheel
RUN cd ./gluesync-sdk && \
    python -m pip install -e .

# Copy the installed SDK to the wheels directory
RUN cd /usr/local/lib/python3.11/site-packages && \
    tar -czf /wheels/gluesync-sdk.tar.gz gluesync_sdk*

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Install required system packages
RUN apt-get update && apt-get install -y \
    cron \
    curl \
    procps && \
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
    CORE_HUB_URL= \
    ENTITY_START_TIMEOUT=2 \
    HOST=0.0.0.0 \
    PORT=1717 \
    DEBUG=False \
    DATA_DIR=/app/data \
    DB_URL=sqlite:///./data/scheduler.db \
    ALLOWED_ORIGINS=* \
    CRONTAB_USER=root \
    GLUESYNC_LICENSE_FILE=/opt/gluesync/data/gs-license.dat \
    SSL_ENABLED=False \
    GLUESYNC_SECURITY_CONFIG=/opt/gluesync/data/security-config.json \
    SSL_SKIP_VERIFY=True

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    curl \
    openssl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy wheels from builder stage
COPY --from=builder /wheels /wheels

# Copy only necessary application files
COPY ./requirements.txt .
COPY ./entrypoint.sh .
COPY ./app.py .
COPY ./config.py .
COPY ./models.py .
COPY ./schemas.py .
COPY ./play_pause.py .
COPY ./gluesync_sdk_client.py .
COPY ./database.py .

# Copy API and services directories
COPY ./api ./api
COPY ./services ./services

# Copy the job_runner.py and run_job.sh scripts
COPY ./job_runner.py /app/job_runner.py
COPY ./run_job.sh /app/run_job.sh

# Make scripts executable
RUN chmod +x /app/entrypoint.sh && \
    chmod +x /app/job_runner.py && \
    chmod +x /app/run_job.sh

# Install specific websockets version first to avoid compatibility issues
RUN python3 -m pip install websockets==11.0.3

# Install the SDK from the tarball
RUN mkdir -p /tmp/sdk && \
    tar -xzf /wheels/gluesync-sdk.tar.gz -C /tmp/sdk && \
    cp -r /tmp/sdk/* /usr/local/lib/python3.11/site-packages/ && \
    rm -rf /tmp/sdk

# Install other Python dependencies
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Expose the port the app runs on
EXPOSE 1717

# Set entrypoint to ensure cron service starts
ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
# Use python directly to ensure our SSL extraction code runs
CMD ["python", "app.py"]
