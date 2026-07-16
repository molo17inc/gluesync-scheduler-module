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


def run_migrations():
    """
    Run all pending database migrations in the correct order.

    This function executes the migration functions directly (not as subprocess)
    to ensure the database schema is up to date.
    """
    try:
        # Get the project root directory - find it by looking for pyproject.toml
        current_path = os.path.abspath(os.path.dirname(__file__))
        project_root = None

        # Walk up the directory tree looking for pyproject.toml
        path_parts = current_path.split(os.sep)
        for i in range(len(path_parts) - 1, -1, -1):
            candidate_root = os.sep.join(path_parts[:i+1])
            if os.path.exists(os.path.join(candidate_root, 'pyproject.toml')):
                project_root = candidate_root
                break

        # Fallback to environment variable or relative path calculation
        if not project_root:
            # Try environment variable first
            project_root = os.environ.get('PROJECT_ROOT')
            if not project_root:
                # Fallback to relative calculation
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

        logger.info(f"Using project root: {project_root}")

        # Get the migrations directory
        migrations_dir = os.path.join(project_root, "migrations")

        if not os.path.exists(migrations_dir):
            logger.warning(f"Migrations directory not found: {migrations_dir}")
            return

        logger.info("Starting database migrations...")

        # Define the migration functions in the order they should be run
        migration_functions = [
            ("migrate_add_settings_table", "create_settings_table"),
            ("migrate_add_snapshot_write_method", "add_snapshot_write_method_column"),
            ("migrate_add_is_cron_expression", "add_is_cron_expression_column"),
            ("migrate_add_group_ids_field", "migrate_add_group_ids_field"),
            ("migrate_add_webhook_timeout_seconds", "add_webhook_timeout_seconds_column"),
            ("migrate_add_chained_events", "create_chained_job_events_table"),
            ("migrate_add_trigger_flows", "migrate_add_trigger_flows"),
            ("migrate_add_execution_logs", "create_execution_logs_table"),
            ("migrate_add_settings_updated_at", "add_settings_updated_at_column"),
        ]

        # Import the database engine from the main app
        try:
            sys.path.insert(0, os.path.join(project_root, "gluesync_scheduler"))
            from gluesync_scheduler.db.database import engine
            logger.info("Database engine imported successfully")
        except ImportError as e:
            logger.error(f"Failed to import database engine: {e}")
            return

        # Run each migration function
        for module_name, function_name in migration_functions:
            script_path = os.path.join(migrations_dir, f"{module_name}.py")

            if not os.path.exists(script_path):
                logger.warning(f"Migration script not found: {module_name}.py")
                continue

            logger.info(f"Running migration: {module_name}")

            try:
                # Load the migration module directly
                spec = importlib.util.spec_from_file_location(module_name, script_path)
                if spec is None or spec.loader is None:
                    logger.error(f"Could not load migration module: {module_name}")
                    continue

                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Get the migration function
                if not hasattr(module, function_name):
                    logger.error(f"Migration function {function_name} not found in {module_name}")
                    continue

                migration_func = getattr(module, function_name)

                # Call the migration function with the engine
                migration_func(engine)

                logger.info(f"Migration {module_name} completed successfully")

            except Exception as e:
                logger.error(f"Error running migration {module_name}: {e}")
                # Continue with other migrations even if one fails

        logger.info("Database migrations completed")

    except Exception as e:
        logger.error(f"Error during migration process: {e}")
        raise
