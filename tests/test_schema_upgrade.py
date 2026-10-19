#!/usr/bin/env python3
"""
Schema upgrade guard: every supported starting database must reach the
current models' schema in ONE startup.

Each baseline in tests/data/schemas is the schema an older release created.
When a model gains a column, the matching migration must be registered in
gluesync_scheduler.db.migrate.MIGRATIONS, ordered after the migration that
creates its table, or these tests fail.
"""

import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gluesync_scheduler.db.database as database
from gluesync_scheduler.db.migrate import MIGRATIONS, prepare_database, run_migrations
from gluesync_scheduler.models.models import (
    Base,
    ChainedJobEvent,
    ExecutionMode,
    ScheduledJob,
    TaskType,
)
from gluesync_scheduler.services.job_service import JobService
from migrations.migrate_add_day_or import add_day_or_column

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = Path(__file__).resolve().parent / "data" / "schemas"

# None is a fresh install (empty database).
BASELINES = [None] + sorted(p.name for p in SCHEMAS_DIR.glob("*.sql"))
BASELINE_IDS = ["fresh-install"] + [name[: -len(".sql")] for name in BASELINES[1:]]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """A file-backed SQLite engine that the migration runner also uses."""
    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'scheduler.db'}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(database, "engine", test_engine)
    yield test_engine
    test_engine.dispose()


def _load_baseline(engine, baseline):
    if baseline is None:
        return
    with sqlite3.connect(engine.url.database) as conn:
        conn.executescript((SCHEMAS_DIR / baseline).read_text())


def _missing_columns(engine, only_existing_tables=False):
    """Map table -> model columns absent from the database."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            if not only_existing_tables:
                missing[table.name] = ["<table>"]
            continue
        have = {col["name"] for col in inspector.get_columns(table.name)}
        absent = [col.name for col in table.columns if col.name not in have]
        if absent:
            missing[table.name] = absent
    return missing


def _migration_errors(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def test_every_migration_script_is_registered_once():
    on_disk = {p.stem for p in (PROJECT_ROOT / "migrations").glob("migrate_*.py")}
    registered = [module for module, _ in MIGRATIONS]
    assert len(registered) == len(set(registered)), "migration registered twice"
    assert set(registered) == on_disk


@pytest.mark.parametrize("baseline", BASELINES, ids=BASELINE_IDS)
def test_migrations_alone_never_leave_a_partial_table(engine, baseline, caplog):
    """
    run_migrations() is also run on its own (run_migrations.sh, older startup
    order), so every table it creates or alters must end up complete, with no
    migration failing because its table does not exist yet.
    """
    _load_baseline(engine, baseline)

    with caplog.at_level(logging.INFO):
        run_migrations()

    assert _migration_errors(caplog) == []
    assert _missing_columns(engine, only_existing_tables=True) == {}


@pytest.mark.parametrize("baseline", BASELINES, ids=BASELINE_IDS)
def test_one_startup_reaches_the_model_schema(engine, baseline, caplog):
    _load_baseline(engine, baseline)

    with caplog.at_level(logging.INFO):
        prepare_database()

    assert _migration_errors(caplog) == []
    assert _missing_columns(engine) == {}


@pytest.mark.parametrize("baseline", BASELINES, ids=BASELINE_IDS)
def test_jobs_with_chained_events_list_after_one_startup(engine, baseline):
    _load_baseline(engine, baseline)
    prepare_database()

    session = sessionmaker(bind=engine)()
    try:
        job = ScheduledJob(
            name="nightly",
            task_type=TaskType.PIPELINE_START,
            cron_expression="0 3 * * *",
            pipeline_id="pipeline-1",
            enabled=True,
            command="pending",
            cron_job_identifier="gluesync_job_nightly",
            snapshot_write_method="UPSERT",
        )
        session.add(job)
        session.flush()
        session.add(ChainedJobEvent(
            parent_job_id=job.id,
            position=0,
            task_type=TaskType.PIPELINE_STOP,
            pipeline_id="pipeline-1",
            execution_mode=ExecutionMode.SYNC,
        ))
        session.commit()

        jobs, total = JobService(session).get_jobs()

        assert total == 1
        assert len(jobs[0].chained_events) == 1
        assert jobs[0].chained_events[0].webhook_timeout_seconds == 3600
    finally:
        session.close()


def test_second_startup_is_a_no_op(engine, caplog):
    _load_baseline(engine, BASELINES[1])
    prepare_database()

    with caplog.at_level(logging.INFO):
        prepare_database()

    assert _migration_errors(caplog) == []
    assert _missing_columns(engine) == {}


def _scheduled_job_next_runs(engine):
    with sqlite3.connect(engine.url.database) as conn:
        return conn.execute("SELECT name, next_run FROM scheduled_jobs ORDER BY id").fetchall()


def _scheduled_job_rows(engine):
    with sqlite3.connect(engine.url.database) as conn:
        return conn.execute("SELECT name, day_or FROM scheduled_jobs ORDER BY id").fetchall()


_LEGACY_JOB_INSERT = (
    "INSERT INTO scheduled_jobs "
    "(name, task_type, cron_expression, pipeline_id, enabled, command, "
    "cron_job_identifier, snapshot_write_method, is_cron_expression, next_run{extra_column}) "
    "VALUES (?, 'PIPELINE_START', '0 3 * * *', 'p1', 1, 'pending', ?, 'UPSERT', 1, "
    "'2026-01-01 03:00:00'{extra_value})"
)


def _insert_legacy_jobs(engine, names):
    with sqlite3.connect(engine.url.database) as conn:
        has_query_read_only = any(
            row[1] == "query_read_only" for row in conn.execute("PRAGMA table_info(scheduled_jobs)")
        )
        statement = _LEGACY_JOB_INSERT.format(
            extra_column=", query_read_only" if has_query_read_only else "",
            extra_value=", 1" if has_query_read_only else "",
        )
        for name in names:
            conn.execute(statement, (name, f"gluesync_job_{name}"))


@pytest.mark.parametrize("baseline", BASELINES[1:], ids=BASELINE_IDS[1:])
def test_day_or_migration_keeps_existing_jobs_on_and(engine, baseline, caplog):
    _load_baseline(engine, baseline)
    _insert_legacy_jobs(engine, ["first", "second"])

    with caplog.at_level(logging.INFO):
        prepare_database()

    assert _scheduled_job_rows(engine) == [("first", 0), ("second", 0)]
    assert "Set day_or=0 (AND) and cleared next_run on 2 existing jobs" in caplog.text
    assert _migration_errors(caplog) == []


@pytest.mark.parametrize("baseline", BASELINES[1:], ids=BASELINE_IDS[1:])
def test_day_or_migration_clears_stored_next_run(engine, baseline):
    """The stored next_run was computed with croniter's OR default: it is recomputed on read."""
    _load_baseline(engine, baseline)
    _insert_legacy_jobs(engine, ["first", "second"])
    assert _scheduled_job_next_runs(engine) == [
        ("first", "2026-01-01 03:00:00"),
        ("second", "2026-01-01 03:00:00"),
    ]

    prepare_database()

    assert _scheduled_job_next_runs(engine) == [("first", None), ("second", None)]


def test_day_or_migration_works_without_a_next_run_column(engine):
    with sqlite3.connect(engine.url.database) as conn:
        conn.execute("CREATE TABLE scheduled_jobs (id INTEGER PRIMARY KEY, name VARCHAR)")
        conn.execute("INSERT INTO scheduled_jobs (name) VALUES ('legacy')")

    add_day_or_column(engine)

    assert _scheduled_job_rows(engine) == [("legacy", 0)]


@pytest.mark.parametrize("baseline", BASELINES[1:], ids=BASELINE_IDS[1:])
def test_day_or_migration_new_jobs_default_to_or_and_rerun_is_a_no_op(engine, baseline):
    _load_baseline(engine, baseline)
    _insert_legacy_jobs(engine, ["legacy"])
    prepare_database()
    session = sessionmaker(bind=engine)()
    try:
        session.add(ScheduledJob(
            name="created-after",
            task_type=TaskType.PIPELINE_START,
            cron_expression="0 3 * * *",
            pipeline_id="p1",
            command="pending",
            cron_job_identifier="gluesync_job_created_after",
        ))
        session.commit()
    finally:
        session.close()

    prepare_database()

    assert _scheduled_job_rows(engine) == [("legacy", 0), ("created-after", 1)]
