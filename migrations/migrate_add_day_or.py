#!/usr/bin/env python3
"""
Migration: Add day_or column to scheduled_jobs table.

When both day-of-month and day-of-week are restricted, day_or=1 fires the job
when either matches (standard cron OR) and day_or=0 requires both (AND).
New jobs default to 1. Jobs that already exist are set to 0 so they keep
firing exactly as they did before this column existed.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def add_day_or_column(engine):
    """Add day_or column to scheduled_jobs and set existing rows to AND (0)."""
    table_sql = text(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='scheduled_jobs'"
    )
    check_sql = text(
        "SELECT count(*) FROM pragma_table_info('scheduled_jobs') "
        "WHERE name = 'day_or'"
    )
    add_sql = text(
        "ALTER TABLE scheduled_jobs "
        "ADD COLUMN day_or BOOLEAN NOT NULL DEFAULT 1"
    )
    keep_and_sql = text("UPDATE scheduled_jobs SET day_or = 0")

    with engine.connect() as conn:
        if not conn.execute(table_sql).scalar():
            logger.info("Table scheduled_jobs does not exist, skipping day_or")
            return
        if conn.execute(check_sql).scalar():
            logger.info("Column day_or already exists, skipping")
            return
        logger.info("Adding day_or column to scheduled_jobs")
        conn.execute(add_sql)
        updated = conn.execute(keep_and_sql).rowcount
        conn.commit()
        logger.info("Set day_or=0 (AND) on %d existing jobs to keep their previous behaviour", updated)
        logger.info("Migration completed successfully")
