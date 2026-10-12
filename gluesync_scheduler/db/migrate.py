#!/usr/bin/env python3
"""
Migration runner for Gluesync Scheduler Module

This module provides a run_migrations() function that can be called from the application
to run all pending database migrations.
"""

import logging
import os
import sys
import importlib.util
from pathlib import Path

logger = logging.getLogger(__name__)

# (module in migrations/, function to call) in the order they should be run.
# A migration that alters a table must come after the migration that
# creates it; tests/test_schema_upgrade.py enforces this.
MIGRATIONS = [
    ("migrate_add_settings_table", "create_settings_table"),
    ("migrate_add_snapshot_write_method", "add_snapshot_write_method_column"),
    ("migrate_add_is_cron_expression", "add_is_cron_expression_column"),
    ("migrate_add_group_ids_field", "migrate_add_group_ids_field"),
    ("migrate_add_chained_events", "create_chained_job_events_table"),
    ("migrate_add_webhook_timeout_seconds", "add_webhook_timeout_seconds_column"),
    ("migrate_add_trigger_flows", "migrate_add_trigger_flows"),
    ("migrate_add_platform_event", "add_platform_event_column"),
    ("migrate_add_trigger_routing", "add_routing_column"),
    ("migrate_add_execution_logs", "create_execution_logs_table"),
    ("migrate_add_settings_updated_at", "add_settings_updated_at_column"),
    ("migrate_add_query_studio_fields", "add_query_studio_fields"),
]


def prepare_database():
    """
    Bring the database schema in line with the models at startup.

    Missing tables are created from the models first, so they always get every
    column; the migrations then only add columns to tables an older release
    created. This is the same order entrypoint.sh uses in the Linux image.
    """
    from gluesync_scheduler.db.database import Base, engine
    import gluesync_scheduler.models.models  # noqa: F401 - registers the tables on Base

    logger.info("Creating database tables if they don't exist...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified/created successfully")

    logger.info("Checking for pending database migrations...")
    try:
        run_migrations()
        logger.info("Database migrations completed successfully")
    except Exception as mig_error:
        logger.warning("Migration check completed (may not be critical): %s", mig_error)


def _find_project_root():
    """Return the directory holding pyproject.toml, else PROJECT_ROOT, else a relative guess."""
    path_parts = os.path.abspath(os.path.dirname(__file__)).split(os.sep)
    for i in range(len(path_parts) - 1, -1, -1):
        candidate_root = os.sep.join(path_parts[:i+1])
        if os.path.exists(os.path.join(candidate_root, 'pyproject.toml')):
            return candidate_root
    return os.environ.get('PROJECT_ROOT') or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )


def _load_migration(migrations_dir, module_name, function_name):
    """Return the migration function, or None (and log why) when it cannot be loaded."""
    script_path = os.path.join(migrations_dir, f"{module_name}.py")
    if not os.path.exists(script_path):
        logger.warning("Migration script not found: %s.py", module_name)
        return None

    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        logger.error("Could not load migration module: %s", module_name)
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, function_name):
        logger.error("Migration function %s not found in %s", function_name, module_name)
        return None
    return getattr(module, function_name)


def _run_migration(engine, migrations_dir, module_name, function_name):
    """Run one migration; a failure is logged and the remaining migrations still run."""
    logger.info("Running migration: %s", module_name)
    try:
        migration_func = _load_migration(migrations_dir, module_name, function_name)
        if migration_func is None:
            return
        migration_func(engine)
        logger.info("Migration %s completed successfully", module_name)
    except Exception as e:
        logger.exception("Error running migration %s: %s", module_name, e)


def run_migrations():
    """
    Run all pending database migrations in the correct order.

    This function executes the migration functions directly (not as subprocess)
    to ensure the database schema is up to date.
    """
    try:
        project_root = _find_project_root()
        logger.info("Using project root: %s", project_root)

        migrations_dir = os.path.join(project_root, "migrations")
        if not os.path.exists(migrations_dir):
            logger.warning("Migrations directory not found: %s", migrations_dir)
            return

        logger.info("Starting database migrations...")

        # Import the database engine from the main app
        try:
            sys.path.insert(0, os.path.join(project_root, "gluesync_scheduler"))
            from gluesync_scheduler.db.database import engine
            logger.info("Database engine imported successfully")
        except ImportError as e:
            logger.exception("Failed to import database engine: %s", e)
            return

        for module_name, function_name in MIGRATIONS:
            _run_migration(engine, migrations_dir, module_name, function_name)

        logger.info("Database migrations completed")

    except Exception as e:
        logger.exception("Error during migration process: %s", e)
        raise
