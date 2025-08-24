#!/bin/bash
set -e

# Only auto-detect timezone if TIMEZONE isn't already set
if [ -z "$TIMEZONE" ]; then
    echo "TIMEZONE not set, auto-detecting..."
    # Linux: Try /etc/timezone
    if [ -f /etc/timezone ]; then
        export TIMEZONE=$(cat /etc/timezone | tr -d '\n')
    # Linux: Try /etc/localtime symlink
    elif [ -L /etc/localtime ]; then
        tz_path=$(readlink /etc/localtime)
        case "$tz_path" in
            *zoneinfo/*)
                export TIMEZONE="${tz_path##*/zoneinfo/}"
                ;;
            *)
                export TIMEZONE="UTC"
                ;;
        esac
    # Windows: Use TZ env if set (Docker for Windows sets this if --env TZ=...)
    elif [ ! -z "$TZ" ]; then
        export TIMEZONE="$TZ"
    else
        export TIMEZONE="UTC"
    fi
else
    echo "Using provided TIMEZONE: $TIMEZONE"
fi

echo "Detected TIMEZONE: $TIMEZONE"

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

# Package is preinstalled from a wheel during image build; skip runtime installation
echo "Package already installed from prebuilt wheel; skipping installation."

# Initialize the database if it doesn't exist
echo "Checking database initialization..."
DATA_DIR=/app/data DB_URL=sqlite:////${DATA_DIR}/scheduler.db python3 -c "from gluesync_scheduler.db.database import engine; from gluesync_scheduler.models.models import Base; Base.metadata.create_all(bind=engine)" || {
    echo "Error initializing database schema"
    exit 1
}
echo "Database schema initialized successfully"

# Run database migrations to ensure schema is up-to-date
echo "Running database migrations..."
# Forward DB URL explicitly for SQLAlchemy-based migrations; group_ids migration uses file path internally
bash /app/migrations/run_migrations.sh --db-url "${DB_URL}" || {
    echo "Error running database migrations"
    exit 1
}
echo "Database migrations completed"

# Execute the CMD command
echo "Starting application..."
exec "$@"
