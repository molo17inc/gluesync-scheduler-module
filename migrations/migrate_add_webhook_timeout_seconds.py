#!/usr/bin/env python3
"""
Migration: Add webhook_timeout_seconds column to chained_job_events table.

This column stores the per-event timeout (in seconds) for waiting on
webhook callbacks in sync mode. Default is 3600 (1 hour).
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def add_webhook_timeout_seconds_column(engine):
    """Add webhook_timeout_seconds column to chained_job_events table."""
    check_sql = text(
        "SELECT count(*) FROM pragma_table_info('chained_job_events') "
        "WHERE name = 'webhook_timeout_seconds'"
    )
    add_sql = text(
        "ALTER TABLE chained_job_events "
        "ADD COLUMN webhook_timeout_seconds INTEGER NOT NULL DEFAULT 3600"
    )

    with engine.connect() as conn:
        exists = conn.execute(check_sql).scalar()
        if exists:
            logger.info("Column webhook_timeout_seconds already exists, skipping")
            return
        logger.info("Adding webhook_timeout_seconds column to chained_job_events")
        conn.execute(add_sql)
        conn.commit()
        logger.info("Migration completed successfully")
