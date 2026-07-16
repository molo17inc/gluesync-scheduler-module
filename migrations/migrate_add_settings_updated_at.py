#!/usr/bin/env python3
"""
Migration: Add updated_at column to settings table.

The settings table was originally created with only created_at. The Setting
response schema expects updated_at, so we add it here with a server default
of CURRENT_TIMESTAMP so existing rows get a valid value.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def add_settings_updated_at_column(engine):
    """Add updated_at column to settings table."""
    check_sql = text(
        "SELECT count(*) FROM pragma_table_info('settings') "
        "WHERE name = 'updated_at'"
    )
    add_sql = text(
        "ALTER TABLE settings "
        "ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
    )

    with engine.connect() as conn:
        exists = conn.execute(check_sql).scalar()
        if exists:
            logger.info("Column updated_at already exists on settings, skipping")
            return
        logger.info("Adding updated_at column to settings")
        conn.execute(add_sql)
        conn.commit()
        logger.info("Migration completed successfully")
