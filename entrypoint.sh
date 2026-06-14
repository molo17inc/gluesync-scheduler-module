#!/bin/sh
set -e

# ---------------------------------------------------------------------------
# Upgrade-path ownership fix (rootful → rootless migration)
# ---------------------------------------------------------------------------
# When upgrading from an image that ran as root, Docker volumes may contain
# files owned by UID 0.  Since the container now runs as 'gluesync' (UID 1017)
# it cannot write to those files.  We detect this by checking whether we were
# started as root and, if so, chown all persistent directories before
# dropping privileges via gosu.  On a fresh install the directories are
# already owned by gluesync (set during image build), so the chown is a
# fast no-op.
# ---------------------------------------------------------------------------
if [ "$(id -u)" = "0" ]; then
    echo "[entrypoint] Running as root — fixing volume ownership for 'gluesync' (upgrade path)..."
    chown -R gluesync:gluesync /app /opt/gluesync 2>/dev/null || true
    chmod +x /app/run_job.sh 2>/dev/null || true
    chmod +x /app/run_scheduler.py 2>/dev/null || true
    echo "[entrypoint] Ownership fixed — dropping privileges to 'gluesync'..."
    exec gosu gluesync "$0" "$@"
fi

# From here on the process runs as 'gluesync' (non-root).

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
