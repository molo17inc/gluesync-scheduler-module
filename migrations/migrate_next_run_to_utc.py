#!/usr/bin/env python3
"""
Migration: convert the stored scheduled_jobs.next_run values to UTC.

SQLite drops the offset of a timezone-aware datetime, and next_run used to be
written as an aware datetime in the job's own timezone: a Europe/Rome 03:10+01:00
was stored as the wall time 03:10, while every reader (the catch-up at startup,
the API serialisation) treats what SQLite returns as UTC. The model now stores
UTC (UTCDateTime), so the rows written before that are converted here, once:
the timezone of a row is its timezone_name, else the global timezone setting,
else TZ/TIMEZONE, else UTC. The wall time of the repeated fall-back hour is read
as its first occurrence (fold=0). Rows whose timezone is invalid are kept as
they are and logged.

Completion is recorded in the settings table (key next_run_stored_utc), in the
same transaction as the conversion, so the migration never runs twice: running
it again on UTC values would shift them. On a database without rows (a fresh
install) there is nothing to convert and the marker is just recorded.
"""

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

logger = logging.getLogger(__name__)

MARKER_KEY = "next_run_stored_utc"
_SQLITE_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


def _table_columns(conn, table):
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _global_timezone(conn):
    row = conn.execute(text("SELECT value FROM settings WHERE key = 'timezone'")).fetchone()
    if row and row[0]:
        return row[0]
    return os.getenv("TZ") or os.getenv("TIMEZONE") or "UTC"


def _to_utc(stored, tz_name):
    """The UTC wall time (SQLite text) of a stored job-local wall time."""
    wall = datetime.fromisoformat(str(stored)).replace(tzinfo=None)
    local = wall.replace(tzinfo=ZoneInfo(tz_name), fold=0)
    return local.astimezone(timezone.utc).strftime(_SQLITE_FORMAT)


def _convert_rows(conn):
    columns = _table_columns(conn, "scheduled_jobs")
    if "next_run" not in columns:
        return 0
    tz_column = "timezone_name" if "timezone_name" in columns else "NULL"
    rows = conn.execute(text(
        f"SELECT id, next_run, {tz_column} FROM scheduled_jobs WHERE next_run IS NOT NULL"
    )).fetchall()
    default_tz = _global_timezone(conn)
    converted = 0
    for job_id, stored, tz_name in rows:
        tz_name = tz_name or default_tz
        try:
            utc_value = _to_utc(stored, tz_name)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as e:
            logger.warning("Job %s: next_run %r kept as stored, cannot convert from timezone %r: %s",
                           job_id, stored, tz_name, e)
            continue
        conn.execute(text("UPDATE scheduled_jobs SET next_run = :value WHERE id = :id"),
                     {"value": utc_value, "id": job_id})
        converted += 1
    return converted


def convert_next_run_to_utc(engine):
    """Convert existing scheduled_jobs.next_run values from job-local wall time to UTC, once."""
    with engine.begin() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "settings" not in tables:
            logger.warning("Settings table does not exist, cannot record %s: skipping", MARKER_KEY)
            return
        if conn.execute(text("SELECT count(*) FROM settings WHERE key = :key"), {"key": MARKER_KEY}).scalar():
            logger.info("next_run already stored as UTC, skipping")
            return

        converted = _convert_rows(conn) if "scheduled_jobs" in tables else 0

        now = datetime.now(timezone.utc).strftime(_SQLITE_FORMAT)
        conn.execute(
            text(
                "INSERT INTO settings (key, value, description, created_at, updated_at) "
                "VALUES (:key, :value, :description, :now, :now)"
            ),
            {
                "key": MARKER_KEY,
                "value": now,
                "description": "scheduled_jobs.next_run values were converted to UTC (internal marker)",
                "now": now,
            },
        )
        logger.info("Converted next_run to UTC on %d existing jobs", converted)
