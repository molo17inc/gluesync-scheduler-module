#!/bin/bash
set -e

# Create necessary directories
mkdir -p /app/data
mkdir -p /app/logs
chmod -R 777 /app/data
chmod -R 777 /app/logs

# Make sure run_job.sh and run_scheduler.py are executable
if [ -f "/app/run_job.sh" ]; then
    echo "Making run_job.sh executable"
    chmod +x /app/run_job.sh
fi

if [ -f "/app/run_scheduler.py" ]; then
    echo "Making run_scheduler.py executable"
    chmod +x /app/run_scheduler.py
fi

# Install the package in development mode
echo "Installing the package in development mode..."
pip3 install -e . || {
    echo "Error installing the package"
    exit 1
}
echo "Package installed successfully"

# Initialize the database if it doesn't exist
echo "Checking database initialization..."
DATA_DIR=/app/data DB_URL=sqlite:////${DATA_DIR}/scheduler.db python3 -c "from gluesync_scheduler.db.database import engine; from gluesync_scheduler.models.models import Base; Base.metadata.create_all(bind=engine)" || {
    echo "Error initializing database schema"
    exit 1
}
echo "Database schema initialized successfully"

# Start cron service
echo "Starting cron service..."
service cron start
echo "Cron service started"

# Execute the CMD command
echo "Starting application..."
exec "$@"
