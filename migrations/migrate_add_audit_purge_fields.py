#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2026 MOLO17. All rights reserved.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("scheduled_jobs", "audit_older_than_days", "INTEGER"),
    ("chained_job_events", "audit_older_than_days", "INTEGER"),
    ("trigger_flow_events", "audit_older_than_days", "INTEGER"),
)


def _table_exists(conn, table_name):
    return bool(conn.execute(
        text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    ).scalar())


def _column_exists(conn, table_name, column_name):
    return bool(conn.execute(
        text(f"SELECT count(*) FROM pragma_table_info('{table_name}') WHERE name = :column_name"),
        {"column_name": column_name},
    ).scalar())


def add_audit_purge_fields(engine):
    """Add the purge age column used by pipeline_audit_purge."""
    with engine.connect() as conn:
        for table_name, column_name, column_type in _COLUMNS:
            if not _table_exists(conn, table_name):
                logger.info("Table %s does not exist, skipping %s", table_name, column_name)
                continue
            if _column_exists(conn, table_name, column_name):
                logger.info("Column %s.%s already exists, skipping", table_name, column_name)
                continue
            logger.info("Adding %s column to %s", column_name, table_name)
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
        conn.commit()
        logger.info("Audit purge fields migration completed")
