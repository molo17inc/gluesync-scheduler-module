#!/bin/bash
set -e

# Create necessary directories
mkdir -p /app/logs
chmod 777 /app/logs

# Initialize the database if it doesn't exist
echo "Checking database initialization..."
python -c "from database import engine; from models import Base; Base.metadata.create_all(bind=engine)" || {
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
