#!/bin/sh
# This script runs all migrations in order.
#
# The order lives in one place only: migration_functions in
# gluesync_scheduler/db/migrate.py. This script calls that runner instead of
# keeping its own list, so the two cannot drift apart.
#
# Usage: run_migrations.sh [--db-url URL]   (defaults to $DB_URL, then DATA_DIR)

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "$0" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

while [ $# -gt 0 ]; do
    case "$1" in
        --db-url) DB_URL="$2"; shift 2 ;;
        --db-url=*) DB_URL="${1#--db-url=}"; shift ;;
        *) shift ;;
    esac
done
if [ -n "$DB_URL" ]; then
    export DB_URL
fi

echo "Running migrations..."
PROJECT_ROOT="$PROJECT_ROOT" PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c "
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# Disaster recovery (opt-in via GS_DR_CONTROL_URL): wait until Core Hub is the serving active node
from gluesync_scheduler import dr_guard
dr_guard.activate()
from gluesync_scheduler.db.migrate import run_migrations
run_migrations()
" || exit 1
echo "All migrations completed."
