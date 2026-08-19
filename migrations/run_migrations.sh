#!/bin/sh
# This script runs all migrations in order

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "$0" )" && pwd )"
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

# Add group_ids column migration (expects a SQLite file path)
echo "Running migration: migrate_add_group_ids_field.py"
python3 "$SCRIPT_DIR/migrate_add_group_ids_field.py" "$PROJECT_ROOT/data/scheduler.db"

# Add webhook_timeout_seconds column migration
echo "Running migration: migrate_add_webhook_timeout_seconds.py"
python3 "$SCRIPT_DIR/migrate_add_webhook_timeout_seconds.py" "$@"

# Add chained_job_events table migration
echo "Running migration: migrate_add_chained_events.py"
python3 "$SCRIPT_DIR/migrate_add_chained_events.py" "$@"

# Add trigger_flows and trigger_flow_events tables
echo "Running migration: migrate_add_trigger_flows.py"
python3 "$SCRIPT_DIR/migrate_add_trigger_flows.py" "$@"

# Add platform_event column to trigger_flows table
echo "Running migration: migrate_add_platform_event.py"
python3 "$SCRIPT_DIR/migrate_add_platform_event.py" "$@"

# Add trigger_flow_execution_logs table
echo "Running migration: migrate_add_execution_logs.py"
python3 "$SCRIPT_DIR/migrate_add_execution_logs.py" "$@"

# Add updated_at column to settings table
echo "Running migration: migrate_add_settings_updated_at.py"
python3 "$SCRIPT_DIR/migrate_add_settings_updated_at.py" "$@"

echo "All migrations completed."
