#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("scheduled_jobs", "viz_id", "VARCHAR"),
    ("scheduled_jobs", "visualize_parameters", "TEXT"),
    ("chained_job_events", "viz_id", "VARCHAR"),
    ("chained_job_events", "visualize_parameters", "TEXT"),
    ("trigger_flow_events", "viz_id", "VARCHAR"),
    ("trigger_flow_events", "visualize_parameters", "TEXT"),
)


def _table_exists(conn, table_name):
    result = conn.execute(
        text(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='table' AND name=:table_name"
        ),
        {"table_name": table_name},
    ).scalar()
    return bool(result)


def _column_exists(conn, table_name, column_name):
    result = conn.execute(
        text(
            f"SELECT count(*) FROM pragma_table_info('{table_name}') "
            "WHERE name = :column_name"
        ),
        {"column_name": column_name},
    ).scalar()
    return bool(result)


def _add_column_if_missing(conn, table_name, column_name, column_type):
    if not _table_exists(conn, table_name):
        logger.info("Table %s does not exist, skipping %s", table_name, column_name)
        return
    if _column_exists(conn, table_name, column_name):
        logger.info("Column %s.%s already exists, skipping", table_name, column_name)
        return
    logger.info("Adding %s column to %s", column_name, table_name)
    conn.execute(text(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
    ))


def add_visualize_refresh_fields(engine):
    """Add visualization id and parameter map columns for visualize_refresh."""
    with engine.connect() as conn:
        for table_name, column_name, column_type in _COLUMNS:
            _add_column_if_missing(conn, table_name, column_name, column_type)
        conn.commit()
        logger.info("Visualize refresh fields migration completed")
