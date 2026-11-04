#!/usr/bin/env python3
"""
Migration: give settings.created_at and settings.updated_at a database default.

migrate_add_settings_table.py used to create the table with Python-side
defaults only, so a database first created by an old release has
created_at/updated_at DATETIME NOT NULL with no DEFAULT. The Setting model
relies on the database default (CURRENT_TIMESTAMP), so inserting a setting
fails there with "NOT NULL constraint failed: settings.created_at" while
updating one works.

SQLite cannot change a column default in place, so the table is rebuilt in one
transaction: rows (ids and timestamps included) are copied into a table with
the fresh-install schema, the old table is dropped and the new one renamed.
Nothing references settings, so no foreign key is affected. A table whose two
columns already have a default is left alone.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

TIMESTAMP_COLUMNS = ("created_at", "updated_at")

# The schema a fresh install gets (Setting in gluesync_scheduler/models/models.py).
CREATE_TABLE_SQL = """
CREATE TABLE settings_new (
    id INTEGER NOT NULL,
    "key" VARCHAR NOT NULL,
    value VARCHAR,
    description VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
)
"""


def _columns_without_default(conn):
    """The timestamp columns of settings that have no DEFAULT (pragma_table_info.dflt_value is NULL)."""
    rows = conn.execute(text("SELECT name, dflt_value FROM pragma_table_info('settings')")).fetchall()
    defaults = {name: dflt_value for name, dflt_value in rows}
    return [name for name in TIMESTAMP_COLUMNS if name in defaults and defaults[name] is None]


def _rebuild_settings_table(conn):
    columns = [row[0] for row in conn.execute(text("SELECT name FROM pragma_table_info('settings_new')"))]
    old_columns = {row[0] for row in conn.execute(text("SELECT name FROM pragma_table_info('settings')"))}
    shared = ", ".join(f'"{name}"' for name in columns if name in old_columns)
    conn.execute(text(f"INSERT INTO settings_new ({shared}) SELECT {shared} FROM settings"))
    conn.execute(text("DROP TABLE settings"))
    conn.execute(text("ALTER TABLE settings_new RENAME TO settings"))
    conn.execute(text('CREATE UNIQUE INDEX ix_settings_key ON settings ("key")'))
    conn.execute(text("CREATE INDEX ix_settings_id ON settings (id)"))


def add_settings_timestamp_defaults(engine):
    """Rebuild settings with DEFAULT CURRENT_TIMESTAMP on created_at and updated_at when it lacks one."""
    if engine.dialect.name != "sqlite":
        logger.info("Database is not SQLite, skipping settings timestamp defaults")
        return

    table_sql = text(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='settings'"
    )

    with engine.connect() as conn:
        if not conn.execute(table_sql).scalar():
            logger.info("Table settings does not exist, skipping settings timestamp defaults")
            return
        missing = _columns_without_default(conn)
        if not missing:
            logger.info("settings.created_at and settings.updated_at already have a default, skipping")
            return

        logger.info("Rebuilding settings: no default on %s", ", ".join(missing))
        # The driver does not open a transaction for DDL, so BEGIN explicitly: either the whole
        # rebuild lands or the original table is left untouched.
        conn.exec_driver_sql("BEGIN")
        try:
            conn.exec_driver_sql("DROP TABLE IF EXISTS settings_new")
            conn.exec_driver_sql(CREATE_TABLE_SQL)
            _rebuild_settings_table(conn)
            conn.exec_driver_sql("COMMIT")
        except Exception:
            conn.exec_driver_sql("ROLLBACK")
            raise
        logger.info("Migration completed successfully")
