#!/bin/sh
set -e

# Only auto-detect timezone if TZ isn't already set
if [ -z "$TZ" ]; then
    echo "TZ not set, auto-detecting..."
    # Linux: Try /etc/timezone
    if [ -f /etc/timezone ]; then
        export TZ=$(cat /etc/timezone | tr -d '\n')
    # Linux: Try /etc/localtime symlink
    elif [ -L /etc/localtime ]; then
        tz_path=$(readlink /etc/localtime)
        case "$tz_path" in
            *zoneinfo/*)
                export TZ="${tz_path##*/zoneinfo/}"
                ;;
            *)
                export TZ="UTC"
                ;;
        esac
    else
        export TZ="UTC"
    fi
else
    echo "Using provided TZ: $TZ"
fi

echo "Detected TZ: $TZ"

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
sh /app/migrations/run_migrations.sh --db-url "${DB_URL}" || {
    echo "Error running database migrations"
    exit 1
}
echo "Database migrations completed"

# Execute the CMD command
echo "Starting application..."
exec "$@"
