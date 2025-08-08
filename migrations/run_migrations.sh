#!/bin/bash
# This script runs all migrations in order

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Run each migration script in sequence
echo "Running migrations..."

# Add settings table migration
echo "Running migration: migrate_add_settings_table.py"
python3 "$SCRIPT_DIR/migrate_add_settings_table.py" "$@"

# Add snapshot_write_method column migration
echo "Running migration: migrate_add_snapshot_write_method.py"
python3 "$SCRIPT_DIR/migrate_add_snapshot_write_method.py" "$@"

# Add is_cron_expression column migration
echo "Running migration: migrate_add_is_cron_expression.py"
python3 "$SCRIPT_DIR/migrate_add_is_cron_expression.py" "$@"

echo "All migrations completed."
