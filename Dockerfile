FROM python:3.11-slim

WORKDIR /app

# Set environment variables for Python, dependency installation, and the application
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Core Hub settings \
    CORE_HUB_URL=http://localhost:1717 \
    ENTITY_START_TIMEOUT=2 \
    # API Server settings \
    HOST=0.0.0.0 \
    PORT=1717 \
    DEBUG=False \
    # Database settings \
    DB_URL=sqlite:///./scheduler.db \
    # CORS settings \
    ALLOWED_ORIGINS=* \
    # Scheduler settings \
    CRONTAB_USER= \
    # Gluesync SDK settings \
    GLUESYNC_LICENSE_FILE=gs-license.dat \
    GLUESYNC_USE_SSL=False \
    GLUESYNC_KEYSTORE_PATH= \
    GLUESYNC_KEYSTORE_PASSWORD=

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc &&
    apt-get clean &&
    rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project files
COPY . .

# Expose the port the app runs on
EXPOSE 1717

# Command to run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "1717"]
