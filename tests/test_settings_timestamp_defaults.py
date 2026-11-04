#!/usr/bin/env python3
"""
settings.created_at / updated_at must have a database default on every install.

The settings table of a database first created by an old release has
created_at/updated_at DATETIME NOT NULL with no DEFAULT (migrate_add_settings_table.py
set Python-side defaults only). The Setting model relies on the database default,
so creating a setting failed there with "NOT NULL constraint failed: settings.created_at".
migrate_add_settings_timestamp_defaults rebuilds the table with the fresh-install schema.
"""

import logging
import os
import sqlite3
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gluesync_scheduler.db.database as database
from gluesync_scheduler.db.migrate import MIGRATIONS, prepare_database
from gluesync_scheduler.models.models import Base, Setting
from migrations.migrate_add_settings_timestamp_defaults import add_settings_timestamp_defaults

# The settings schema seen on an install upgraded from an old release.
LEGACY_SCHEMA = """
CREATE TABLE settings (
	id INTEGER NOT NULL,
	"key" VARCHAR NOT NULL,
	value VARCHAR,
	description VARCHAR,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_settings_key ON settings ("key");
CREATE INDEX ix_settings_id ON settings (id);
"""

ROWS = [
    (1, "timezone", "Europe/Rome", "Timezone used for scheduling jobs", "2025-03-01 10:00:00.000000", "2025-03-02 11:30:00.000000"),
    (7, "custom_flag", "on", None, "2025-04-05 08:15:00.000000", "2025-04-05 08:15:00.000000"),
    (9, "empty_value", None, "no value", "2025-05-06 09:45:30.123456", "2025-06-07 12:00:00.000000"),
]


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'scheduler.db'}", connect_args={"check_same_thread": False})
    yield eng
    eng.dispose()


@pytest.fixture
def legacy_engine(engine):
    with sqlite3.connect(engine.url.database) as conn:
        conn.executescript(LEGACY_SCHEMA)
        conn.executemany("INSERT INTO settings VALUES (?, ?, ?, ?, ?, ?)", ROWS)
    return engine


def _raw(engine, sql):
    with sqlite3.connect(engine.url.database) as conn:
        return conn.execute(sql).fetchall()


def _defaults(engine):
    """Map timestamp column -> DEFAULT of the settings table."""
    return {
        name: dflt
        for _, name, _, _, dflt, _ in _raw(engine, "PRAGMA table_info(settings)")
        if name in ("created_at", "updated_at")
    }


def _indexes(engine):
    """Map index name -> unique flag for the named indexes on settings."""
    return {row[1]: bool(row[2]) for row in _raw(engine, "PRAGMA index_list(settings)") if not row[1].startswith("sqlite_")}


def _insert_setting(engine, key="custom_setting"):
    session = sessionmaker(bind=engine)()
    try:
        setting = Setting(key=key, value="v", description="added after the upgrade")
        session.add(setting)
        session.commit()
        session.refresh(setting)
        return setting.created_at, setting.updated_at
    finally:
        session.close()


def test_legacy_schema_cannot_insert_a_setting_without_the_migration(legacy_engine):
    assert _defaults(legacy_engine) == {"created_at": None, "updated_at": None}

    with pytest.raises(IntegrityError, match="settings.created_at"):
        _insert_setting(legacy_engine)


def test_migration_adds_both_defaults_and_keeps_every_row(legacy_engine):
    add_settings_timestamp_defaults(legacy_engine)

    assert _defaults(legacy_engine) == {"created_at": "CURRENT_TIMESTAMP", "updated_at": "CURRENT_TIMESTAMP"}
    assert _raw(legacy_engine, "SELECT id, key, value, description, created_at, updated_at FROM settings ORDER BY id") == ROWS


def test_migration_recreates_both_indexes_and_the_key_stays_unique(legacy_engine):
    add_settings_timestamp_defaults(legacy_engine)

    assert _indexes(legacy_engine) == {"ix_settings_key": True, "ix_settings_id": False}
    with pytest.raises(IntegrityError, match="settings.key"):
        _insert_setting(legacy_engine, key="timezone")


def test_migration_leaves_no_helper_table_behind(legacy_engine):
    add_settings_timestamp_defaults(legacy_engine)

    tables = {row[0] for row in _raw(legacy_engine, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"settings"}


def test_a_setting_can_be_created_after_the_migration(legacy_engine):
    add_settings_timestamp_defaults(legacy_engine)

    created_at, updated_at = _insert_setting(legacy_engine)

    assert created_at is not None
    assert updated_at is not None
    assert _raw(legacy_engine, "SELECT count(*) FROM settings") == [(4,)]


def test_updating_a_setting_still_works_after_the_migration(legacy_engine):
    add_settings_timestamp_defaults(legacy_engine)

    session = sessionmaker(bind=legacy_engine)()
    try:
        setting = session.query(Setting).filter(Setting.key == "custom_flag").one()
        setting.value = "off"
        session.commit()
    finally:
        session.close()

    assert _raw(legacy_engine, "SELECT value FROM settings WHERE key = 'custom_flag'") == [("off",)]


def test_running_the_migration_twice_is_a_no_op(legacy_engine, caplog):
    add_settings_timestamp_defaults(legacy_engine)
    schema_after_first = _raw(legacy_engine, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
    # A rebuild drops the table and its triggers, so a surviving trigger proves there was none.
    with sqlite3.connect(legacy_engine.url.database) as conn:
        conn.execute("CREATE TRIGGER settings_marker AFTER DELETE ON settings BEGIN SELECT 1; END")

    caplog.clear()
    with caplog.at_level(logging.INFO):
        add_settings_timestamp_defaults(legacy_engine)

    assert "already have a default, skipping" in caplog.text
    assert "Rebuilding settings" not in caplog.text
    assert _raw(legacy_engine, "SELECT name FROM sqlite_master WHERE type = 'trigger'") == [("settings_marker",)]
    assert _raw(legacy_engine, "SELECT type, name, sql FROM sqlite_master WHERE type != 'trigger' ORDER BY name") == schema_after_first
    assert _raw(legacy_engine, "SELECT id, key, value, description, created_at, updated_at FROM settings ORDER BY id") == ROWS


def test_fresh_schema_is_left_untouched(engine, caplog):
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(Setting(key="timezone", value="UTC", description="d"))
        session.commit()
    finally:
        session.close()
    before = _raw(engine, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
    rows_before = _raw(engine, "SELECT * FROM settings")
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("CREATE TRIGGER settings_marker AFTER DELETE ON settings BEGIN SELECT 1; END")

    with caplog.at_level(logging.INFO):
        add_settings_timestamp_defaults(engine)

    assert "already have a default, skipping" in caplog.text
    assert _raw(engine, "SELECT type, name, sql FROM sqlite_master WHERE type != 'trigger' ORDER BY name") == before
    assert _raw(engine, "SELECT * FROM settings") == rows_before
    assert _raw(engine, "SELECT name FROM sqlite_master WHERE type = 'trigger'") == [("settings_marker",)]


def test_missing_settings_table_is_skipped(engine, caplog):
    with caplog.at_level(logging.INFO):
        add_settings_timestamp_defaults(engine)

    assert "Table settings does not exist, skipping" in caplog.text
    assert _raw(engine, "SELECT name FROM sqlite_master") == []


def test_non_sqlite_database_is_skipped(caplog):
    class FakeDialect:
        name = "postgresql"

    class FakeEngine:
        dialect = FakeDialect()

    with caplog.at_level(logging.INFO):
        add_settings_timestamp_defaults(FakeEngine())

    assert "not SQLite, skipping" in caplog.text


def test_a_failed_rebuild_rolls_back_and_keeps_the_original_table(legacy_engine):
    # A table named like the index makes the last step fail, after the old table was dropped.
    with sqlite3.connect(legacy_engine.url.database) as conn:
        conn.execute("DROP INDEX ix_settings_id")
        conn.execute("CREATE TABLE ix_settings_id (x INTEGER)")

    with pytest.raises(Exception):
        add_settings_timestamp_defaults(legacy_engine)

    assert _defaults(legacy_engine) == {"created_at": None, "updated_at": None}
    assert _raw(legacy_engine, "SELECT id, key, value, description, created_at, updated_at FROM settings ORDER BY id") == ROWS
    assert "settings_new" not in {row[0] for row in _raw(legacy_engine, "SELECT name FROM sqlite_master")}


def test_migration_is_registered_after_the_settings_table_migrations():
    order = [module for module, _ in MIGRATIONS]
    position = order.index("migrate_add_settings_timestamp_defaults")

    assert position > order.index("migrate_add_settings_table")
    assert position > order.index("migrate_add_settings_updated_at")


def test_startup_on_the_legacy_schema_allows_creating_a_setting(legacy_engine, monkeypatch, caplog):
    monkeypatch.setattr(database, "engine", legacy_engine)

    with caplog.at_level(logging.INFO):
        prepare_database()

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert _defaults(legacy_engine) == {"created_at": "CURRENT_TIMESTAMP", "updated_at": "CURRENT_TIMESTAMP"}
    created_at, updated_at = _insert_setting(legacy_engine)
    assert created_at is not None and updated_at is not None
    assert _raw(legacy_engine, "SELECT id FROM settings WHERE id IN (1, 7, 9) ORDER BY id") == [(1,), (7,), (9,)]
