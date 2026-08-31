#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Gluesync Scheduler Module (aka Chronos) is dual-licensed under the following licenses:
 *
 * 1. GNU General Public License (GPL) Version 3
 *    You may use, modify, and distribute this software under the terms of the GPL v3.
 *    See the LICENSE-GPL file or <http://www.gnu.org/licenses/gpl-3.0.html> for details.
 *    This option is available at no cost, but any derivative works must also be licensed under GPL v3.
 *
 * 2. MOLO17 Commercial License
 *    Alternatively, you may use this software under the MOLO17 Commercial License,
 *    which includes a warranty and permits proprietary use. Contact MOLO17 at info@molo17.com
 *    for licensing terms and conditions.
 *
 * You must choose one of these licenses to use this software. Using this software implies
 * acceptance of one of these licenses. See the accompanying LICENSE files or contact
 * MOLO17 for more information.
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUMNS = (
    ("scheduled_jobs", "agent_id", "VARCHAR"),
    ("scheduled_jobs", "query_sql", "TEXT"),
    ("scheduled_jobs", "saved_query_id", "VARCHAR"),
    ("chained_job_events", "agent_id", "VARCHAR"),
    ("chained_job_events", "query_sql", "TEXT"),
    ("chained_job_events", "saved_query_id", "VARCHAR"),
    ("trigger_flow_events", "agent_id", "VARCHAR"),
    ("trigger_flow_events", "query_sql", "TEXT"),
    ("trigger_flow_events", "saved_query_id", "VARCHAR"),
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


def add_query_studio_fields(engine):
    """Add nullable agent_id / query_sql / saved_query_id columns used by Query Studio tasks."""
    with engine.connect() as conn:
        for table_name, column_name, column_type in _COLUMNS:
            _add_column_if_missing(conn, table_name, column_name, column_type)
        conn.commit()
        logger.info("Query Studio fields migration completed")
