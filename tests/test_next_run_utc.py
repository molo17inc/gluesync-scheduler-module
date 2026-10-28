#!/usr/bin/env python3
"""
GSSD-1457: scheduled_jobs datetimes are stored as UTC.

SQLite drops the offset of an aware datetime, so next_run (written in the job's
timezone) used to be stored as local wall time while every reader treats naive
as UTC. UTCDateTime converts aware values on write; the migration converts the
rows written before that, once.
"""

import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Integer, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gluesync_scheduler.db.database as database
from gluesync_scheduler.db.migrate import MIGRATIONS, prepare_database
from gluesync_scheduler.models.models import ScheduledJob, TaskType
from gluesync_scheduler.models.utc_datetime import UTCDateTime
from migrations.migrate_next_run_to_utc import MARKER_KEY, convert_next_run_to_utc

ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc

Base = declarative_base()


class Stamp(Base):
    __tablename__ = "stamps"
    id = Column(Integer, primary_key=True)
    at = Column(UTCDateTime, nullable=True)


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'scheduler.db'}", connect_args={"check_same_thread": False})
    yield eng
    eng.dispose()


def _raw(engine, sql):
    with sqlite3.connect(engine.url.database) as conn:
        return conn.execute(sql).fetchall()


# ---------------------------------------------------------------------------
# UTCDateTime
# ---------------------------------------------------------------------------

def test_aware_datetime_is_stored_as_utc_and_read_back_naive_utc(engine):
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    # 2026-10-25 03:10 CET (UTC+1), right after the clocks went back
    session.add(Stamp(id=1, at=datetime(2026, 10, 25, 3, 10, tzinfo=ROME)))
    session.commit()
    session.close()

    assert _raw(engine, "SELECT at FROM stamps") == [("2026-10-25 02:10:00.000000",)]
    session = sessionmaker(bind=engine)()
    assert session.get(Stamp, 1).at == datetime(2026, 10, 25, 2, 10)
    session.close()


def test_summer_offset_is_converted_too(engine):
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Stamp(id=1, at=datetime(2026, 6, 10, 14, 30, tzinfo=ROME)))   # CEST, UTC+2
    session.commit()
    session.close()
    assert _raw(engine, "SELECT at FROM stamps") == [("2026-06-10 12:30:00.000000",)]


def test_naive_and_utc_values_are_stored_unchanged(engine):
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        Stamp(id=1, at=datetime(2026, 6, 10, 12, 30)),
        Stamp(id=2, at=datetime(2026, 6, 10, 12, 30, tzinfo=UTC)),
        Stamp(id=3, at=None),
    ])
    session.commit()
    session.close()
    assert _raw(engine, "SELECT id, at FROM stamps ORDER BY id") == [
        (1, "2026-06-10 12:30:00.000000"),
        (2, "2026-06-10 12:30:00.000000"),
        (3, None),
    ]


def test_scheduled_job_next_run_is_stored_as_utc(engine):
    database.Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(ScheduledJob(
        name="exit", task_type=TaskType.PIPELINE_EXIT_MAINTENANCE, cron_expression="10 3 * * 0",
        pipeline_id="p1", command="pending", cron_job_identifier="gluesync_job_exit",
        next_run=datetime(2026, 10, 25, 3, 10, tzinfo=ROME),
        last_run=datetime(2026, 10, 25, 3, 10, tzinfo=ROME),
        last_successful_run=datetime(2026, 10, 25, 3, 10, tzinfo=ROME),
        last_run_error_time=datetime(2026, 10, 25, 3, 10, tzinfo=ROME),
    ))
    session.commit()
    session.close()

    row = _raw(engine, "SELECT next_run, last_run, last_successful_run, last_run_error_time FROM scheduled_jobs")[0]
    assert set(row) == {"2026-10-25 02:10:00.000000"}


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

def _create_legacy_db(engine, jobs, global_tz=None):
    """A database as the previous release left it: next_run holds job-local wall time."""
    database.Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        if global_tz:
            conn.execute(text(
                "INSERT INTO settings (key, value) VALUES ('timezone', :tz)"), {"tz": global_tz})
        for i, (next_run, tz_name) in enumerate(jobs, start=1):
            conn.execute(text(
                "INSERT INTO scheduled_jobs (name, task_type, cron_expression, pipeline_id, enabled, command, "
                "cron_job_identifier, snapshot_write_method, is_cron_expression, query_read_only, "
                "allow_ai_run_loop, validation_reconcile, day_or, next_run, timezone_name) "
                "VALUES (:name, 'PIPELINE_START', '10 3 * * 0', 'p1', 1, 'pending', :ident, 'UPSERT', 1, 1, "
                "0, 0, 1, :next_run, :tz)"),
                {"name": f"job{i}", "ident": f"gluesync_job_{i}", "next_run": next_run, "tz": tz_name})


def _next_runs(engine):
    return [r[0] for r in _raw(engine, "SELECT next_run FROM scheduled_jobs ORDER BY id")]


def _marker_rows(engine):
    return _raw(engine, f"SELECT key FROM settings WHERE key = '{MARKER_KEY}'")


def test_migration_is_registered_last_after_the_settings_migration():
    names = [module for module, _ in MIGRATIONS]
    assert names[-1] == "migrate_next_run_to_utc"
    assert names.index("migrate_add_settings_table") < names.index("migrate_next_run_to_utc")


def test_migration_converts_wall_time_rows_to_utc(engine, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    _create_legacy_db(engine, [
        ("2026-10-25 03:10:00.000000", "Europe/Rome"),    # CET: 02:10 UTC
        ("2026-06-10 14:30:00.000000", "Europe/Rome"),    # CEST: 12:30 UTC
        ("2026-06-10 14:30:00", "Asia/Tokyo"),            # east, no fractional part: 05:30 UTC
        ("2026-06-10 14:30:00.000000", "America/New_York"),   # west: 18:30 UTC
        ("2026-06-10 14:30:00.000000", "UTC"),
        ("2026-06-10 14:30:00.000000", None),             # no job timezone: global setting
        (None, "Europe/Rome"),
    ], global_tz="Asia/Kolkata")

    convert_next_run_to_utc(engine)

    assert _next_runs(engine) == [
        "2026-10-25 02:10:00.000000",
        "2026-06-10 12:30:00.000000",
        "2026-06-10 05:30:00.000000",
        "2026-06-10 18:30:00.000000",
        "2026-06-10 14:30:00.000000",
        "2026-06-10 09:00:00.000000",
        None,
    ]
    assert len(_marker_rows(engine)) == 1


def test_migration_without_job_or_setting_timezone_uses_utc(engine, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    _create_legacy_db(engine, [("2026-06-10 14:30:00.000000", None)])
    convert_next_run_to_utc(engine)
    assert _next_runs(engine) == ["2026-06-10 14:30:00.000000"]


def test_migration_reads_the_ambiguous_fall_back_hour_as_its_first_occurrence(engine):
    # 02:30 on 2026-10-25 happens twice in Rome: fold=0 is the CEST one, 00:30 UTC
    _create_legacy_db(engine, [("2026-10-25 02:30:00.000000", "Europe/Rome")])
    convert_next_run_to_utc(engine)
    assert _next_runs(engine) == ["2026-10-25 00:30:00.000000"]


def test_migration_keeps_rows_with_an_invalid_timezone(engine, caplog):
    _create_legacy_db(engine, [
        ("2026-06-10 14:30:00.000000", "Mars/Olympus"),
        ("2026-06-10 14:30:00.000000", "Europe/Rome"),
    ])
    with caplog.at_level(logging.WARNING):
        convert_next_run_to_utc(engine)
    assert _next_runs(engine) == ["2026-06-10 14:30:00.000000", "2026-06-10 12:30:00.000000"]
    assert "Mars/Olympus" in caplog.text
    assert len(_marker_rows(engine)) == 1


def test_migration_runs_only_once(engine):
    _create_legacy_db(engine, [("2026-06-10 14:30:00.000000", "Europe/Rome")])
    convert_next_run_to_utc(engine)
    convert_next_run_to_utc(engine)
    assert _next_runs(engine) == ["2026-06-10 12:30:00.000000"]
    assert len(_marker_rows(engine)) == 1


def test_values_written_after_the_migration_are_not_converted_again(engine):
    _create_legacy_db(engine, [("2026-06-10 14:30:00.000000", "Europe/Rome")])
    convert_next_run_to_utc(engine)
    session = sessionmaker(bind=engine)()
    session.add(ScheduledJob(
        name="new", task_type=TaskType.PIPELINE_START, cron_expression="30 14 * * *", pipeline_id="p1",
        command="pending", cron_job_identifier="gluesync_job_new", timezone_name="Europe/Rome",
        next_run=datetime(2026, 6, 11, 14, 30, tzinfo=ROME),
    ))
    session.commit()
    session.close()

    convert_next_run_to_utc(engine)

    assert _next_runs(engine) == ["2026-06-10 12:30:00.000000", "2026-06-11 12:30:00.000000"]


def test_migration_on_a_fresh_database_only_records_the_marker(engine):
    from migrations.migrate_add_settings_table import create_settings_table
    create_settings_table(engine)    # no scheduled_jobs table at all

    convert_next_run_to_utc(engine)

    assert len(_marker_rows(engine)) == 1


def test_migration_on_a_database_with_empty_tables(engine):
    database.Base.metadata.create_all(bind=engine)
    convert_next_run_to_utc(engine)
    convert_next_run_to_utc(engine)
    assert _next_runs(engine) == []
    assert len(_marker_rows(engine)) == 1


def test_migration_without_a_settings_table_does_nothing(engine):
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE scheduled_jobs (id INTEGER PRIMARY KEY, next_run DATETIME)"))
        conn.execute(text("INSERT INTO scheduled_jobs (next_run) VALUES ('2026-06-10 14:30:00.000000')"))
    convert_next_run_to_utc(engine)
    assert _next_runs(engine) == ["2026-06-10 14:30:00.000000"]


def test_startup_through_the_runner_converts_once(tmp_path, monkeypatch):
    """prepare_database() (create tables, then every migration) on an upgraded database."""
    eng = create_engine(f"sqlite:///{tmp_path / 'scheduler.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    database.Base.metadata.create_all(bind=eng)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO settings (key, value) VALUES ('timezone', 'Europe/Rome')"))
    _insert_exit_job(eng)

    prepare_database()
    prepare_database()

    assert _next_runs(eng) == ["2026-10-25 02:10:00.000000"]
    eng.dispose()


def _insert_exit_job(engine):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO scheduled_jobs (name, task_type, cron_expression, pipeline_id, enabled, command, "
            "cron_job_identifier, snapshot_write_method, is_cron_expression, query_read_only, "
            "allow_ai_run_loop, validation_reconcile, day_or, next_run) "
            "VALUES ('exit', 'PIPELINE_EXIT_MAINTENANCE', '10 3 * * 0', 'p1', 1, 'pending', 'gluesync_job_exit', "
            "'UPSERT', 1, 1, 0, 0, 1, '2026-10-25 03:10:00.000000')"))
