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
    ("scheduled_jobs", "agent_alias", "VARCHAR"),
    ("scheduled_jobs", "agent_version", "INTEGER"),
    ("scheduled_jobs", "agent_input", "TEXT"),
    ("scheduled_jobs", "prompt_template", "TEXT"),
    ("scheduled_jobs", "payload_allow_list", "TEXT"),
    ("scheduled_jobs", "idempotency_key", "VARCHAR"),
    ("scheduled_jobs", "allow_ai_run_loop", "BOOLEAN NOT NULL DEFAULT 0"),
    ("chained_job_events", "agent_alias", "VARCHAR"),
    ("chained_job_events", "agent_version", "INTEGER"),
    ("chained_job_events", "agent_input", "TEXT"),
    ("chained_job_events", "prompt_template", "TEXT"),
    ("chained_job_events", "payload_allow_list", "TEXT"),
    ("chained_job_events", "idempotency_key", "VARCHAR"),
    ("chained_job_events", "allow_ai_run_loop", "BOOLEAN NOT NULL DEFAULT 0"),
    ("trigger_flow_events", "agent_alias", "VARCHAR"),
    ("trigger_flow_events", "agent_version", "INTEGER"),
    ("trigger_flow_events", "agent_input", "TEXT"),
    ("trigger_flow_events", "prompt_template", "TEXT"),
    ("trigger_flow_events", "payload_allow_list", "TEXT"),
    ("trigger_flow_events", "idempotency_key", "VARCHAR"),
    ("trigger_flow_events", "allow_ai_run_loop", "BOOLEAN NOT NULL DEFAULT 0"),
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


def add_ai_agent_run_fields(engine):
    """Add published AI agent run columns used by Chronos ai_agent_run tasks."""
    with engine.connect() as conn:
        for table_name, column_name, column_type in _COLUMNS:
            _add_column_if_missing(conn, table_name, column_name, column_type)
        conn.commit()
        logger.info("AI agent run fields migration completed")
