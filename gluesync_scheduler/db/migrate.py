#!/usr/bin/env python3
"""
Migration runner for Gluesync Scheduler Module

This module provides a run_migrations() function that can be called from the application
to run all pending database migrations.
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def run_migrations():
    """
    Run all pending database migrations in the correct order.

    This function executes the migration scripts in sequence to ensure
    the database schema is up to date.
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

        # Define the migration scripts in the order they should be run
        migration_scripts = [
            "migrate_add_settings_table.py",
            "migrate_add_snapshot_write_method.py",
            "migrate_add_is_cron_expression.py",
            "migrate_add_group_ids_field.py"
        ]

        # Run each migration script
        for script_name in migration_scripts:
            script_path = os.path.join(migrations_dir, script_name)

            if not os.path.exists(script_path):
                logger.warning(f"Migration script not found: {script_name}")
                continue

            logger.info(f"Running migration: {script_name}")

            try:
                # Run the migration script as a subprocess
                result = subprocess.run([
                    sys.executable, script_path
                ], capture_output=True, text=True, cwd=project_root)

                if result.returncode == 0:
                    logger.info(f"Migration {script_name} completed successfully")
                    if result.stdout:
                        logger.debug(f"Migration output: {result.stdout}")
                else:
                    logger.error(f"Migration {script_name} failed with return code {result.returncode}")
                    if result.stderr:
                        logger.error(f"Migration error: {result.stderr}")
                    # Continue with other migrations even if one fails

            except Exception as e:
                logger.error(f"Error running migration {script_name}: {e}")
                # Continue with other migrations

        logger.info("Database migrations completed")

    except Exception as e:
        logger.error(f"Error during migration process: {e}")
        raise
