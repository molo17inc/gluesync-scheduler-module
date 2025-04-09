#!/bin/bash
set -e

# Create necessary directories
mkdir -p /app/data
mkdir -p /app/logs
chmod -R 777 /app/data
chmod -R 777 /app/logs

# Make sure run_job.sh and job_runner.py are executable
if [ -f "/app/run_job.sh" ]; then
    echo "Making run_job.sh executable"
    chmod +x /app/run_job.sh
fi

if [ -f "/app/job_runner.py" ]; then
    echo "Making job_runner.py executable"
    chmod +x /app/job_runner.py
fi

# Initialize the database if it doesn't exist
echo "Checking database initialization..."
DATA_DIR=/app/data DB_URL=sqlite:////${DATA_DIR}/scheduler.db python3 -c "from database import engine; from models import Base; Base.metadata.create_all(bind=engine)" || {
    echo "Error initializing database schema"
    exit 1
}
echo "Database schema initialized successfully"

# List all files in the app directory for debugging
echo "Listing files in /app:"
ls -la /app

# Start cron service
echo "Starting cron service..."
service cron start
echo "Cron service started"

# Execute the CMD command
echo "Starting application..."
exec "$@"
