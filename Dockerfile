# Build stage for SDK installation
FROM python:3.11-slim AS builder

WORKDIR /build

# Set environment variables for Python and dependency installation
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    python3-dev \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy only the SDK submodule
COPY ./gluesync-sdk ./gluesync-sdk

# Install specific websockets version first to avoid compatibility issues
RUN pip install websockets==11.0.3

# Install the SDK from the submodule and create a wheel
RUN pip install wheel && \
    cd ./gluesync-sdk && \
    pip wheel -w /wheels .

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Create Gluesync default directories and app data directory
RUN mkdir -p /opt/gluesync/data && \
    mkdir -p /app/data && \
    chmod 777 /app/data

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Core Hub settings \
    CORE_HUB_URL= \
    ENTITY_START_TIMEOUT=2 \
    # API Server settings \
    HOST=0.0.0.0 \
    PORT=1717 \
    DEBUG=False \
    # Database settings \
    DATA_DIR=/app/data \
    DB_URL=sqlite:///./data/scheduler.db \
    # CORS settings \
    ALLOWED_ORIGINS=* \
    # Scheduler settings \
    CRONTAB_USER=root \
    # Gluesync SDK settings using default paths \
    GLUESYNC_LICENSE_FILE=/opt/gluesync/data/gs-license.dat \
    SSL_ENABLED=False \
    GLUESYNC_SECURITY_CONFIG=/opt/gluesync/data/security-config.json \
    SSL_SKIP_VERIFY=True

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    curl \
    openssl \
    && apt-get clean && \
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
COPY ./api ./api
COPY ./services ./services

# Create logs directory
RUN mkdir -p /app/logs

# Make entrypoint script executable
RUN chmod +x /app/entrypoint.sh

# Install specific websockets version first to avoid compatibility issues
RUN pip install websockets==11.0.3

# Install the SDK from the wheel
RUN pip install /wheels/*

# Install other Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port the app runs on
EXPOSE 1717

# Set entrypoint to ensure cron service starts
ENTRYPOINT ["/app/entrypoint.sh"]

# Command to run the application
# Use python directly to ensure our SSL extraction code runs
CMD ["python", "app.py"]
