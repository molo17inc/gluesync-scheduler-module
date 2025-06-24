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

# List the site-packages directory to see what's installed
RUN ls -la /usr/local/lib/python3.11/site-packages

# Copy the installed SDK to the wheels directory - use a more general approach
RUN cd /usr/local/lib/python3.11/site-packages && \
    find . -name "*gluesync*" -o -name "*twofish*" | tar -czf /wheels/gluesync-sdk.tar.gz -T -

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Install required system packages including timezone data
RUN apt-get update && apt-get install -y \
    cron \
    curl \
    procps \
    tzdata && \
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
    LOG_LEVEL=DEBUG \
    DATA_DIR=/app/data \
    DB_URL=sqlite:///./data/scheduler.db \
    ALLOWED_ORIGINS=* \
    CRONTAB_USER=root \
    GLUESYNC_LICENSE_FILE=/opt/gluesync/data/gs-license.dat \
    SSL_ENABLED=False \
    GLUESYNC_SECURITY_CONFIG=/opt/gluesync/data/security-config.json \
    SSL_SKIP_VERIFY=True \
    TIMEZONE=UTC

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    openssl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the SDK code to the final stage
COPY --from=builder /wheels /wheels
COPY --from=builder /build/gluesync-sdk /app/gluesync-sdk

# Copy requirements and setup files
COPY ./requirements.txt .
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
RUN chmod +x /app/entrypoint.sh /app/docker-entrypoint.sh

# Install SDK dependencies one by one to avoid issues
RUN python -m pip install --upgrade pip && \
    python -m pip install websockets==11.0.3 && \
    python -m pip install requests>=2.25.1 && \
    python -m pip install python-dateutil>=2.8.1 && \
    python -m pip install PyJWT>=2.0.1 && \
    python -m pip install cryptography>=3.4.6 && \
    python -m pip install pydantic>=1.8.1 && \
    python -m pip install typing-extensions>=3.7.4.3

# Install OpenSSL for certificate handling and required dependencies
RUN apt-get update && apt-get install -y openssl build-essential libssl-dev

# Create Python path file for SDK
RUN mkdir -p /usr/local/lib/python3.11/site-packages/gluesync_sdk && \
    cp -r /app/gluesync-sdk/gluesync_sdk/* /usr/local/lib/python3.11/site-packages/gluesync_sdk/ && \
    touch /usr/local/lib/python3.11/site-packages/gluesync_sdk/__init__.py

# Install other Python dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Expose the port the app runs on
EXPOSE 1717

# Set entrypoint to ensure cron service starts
ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
# Use python3 directly to ensure our SSL extraction code runs
CMD ["python3", "run_scheduler.py"]
