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

GSSD-311 acceptance tests: day-of-month / day-of-week combination.

Standard cron fires when EITHER day-of-month OR day-of-week matches when both
are restricted (croniter day_or=True). Chronos must do that by default and
offer AND through the boolean job field ``day_or=False``.

The trigger tests assert on the real fire times of the APScheduler trigger
built by the real SchedulerService (conftest stubs that module, so it is
loaded here from its file under a private name).
"""

import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta
from typing import List

import pytest
import pytz
import sqlalchemy
from croniter import croniter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base
from gluesync_scheduler.models.models import ScheduledJob, Setting, TaskType
from gluesync_scheduler.models.schemas import Job, JobCreate, JobUpdate

TZ_NAME = "Asia/Tokyo"
TZ = pytz.timezone(TZ_NAME)
WINDOW_START = TZ.localize(datetime(2025, 8, 1))
WINDOW_END = TZ.localize(datetime(2025, 9, 1))
MAX_FIRES = 500

SERVICE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "gluesync_scheduler", "services", "scheduler_service.py"
)


# ---------------------------------------------------------------------------
# Real SchedulerService (the conftest stub must stay in sys.modules)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def service(tmp_path_factory):
    stub = sys.modules.get("gluesync_scheduler.services.scheduler_service")
    previous_log_dir = os.environ.get("CRON_LOG_DIR")
    os.environ["CRON_LOG_DIR"] = str(tmp_path_factory.mktemp("cron_logs"))

    loop = asyncio.new_event_loop()

    async def _import_real_module():
        # AsyncIOScheduler.start() needs a running loop; the module starts it at import time
        spec = importlib.util.spec_from_file_location("_real_scheduler_service", SERVICE_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    module = loop.run_until_complete(_import_real_module())
    instance = module.SchedulerService.get_instance()

    assert sys.modules.get("gluesync_scheduler.services.scheduler_service") is stub
    yield instance

    try:
        instance.scheduler.shutdown(wait=False)
    finally:
        module.SchedulerService._instance = None
        loop.close()
        if previous_log_dir is None:
            os.environ.pop("CRON_LOG_DIR", None)
        else:
            os.environ["CRON_LOG_DIR"] = previous_log_dir


@pytest.fixture(autouse=True)
def _clean_scheduler_jobs(request):
    yield
    if "service" in request.fixturenames:
        request.getfixturevalue("service").scheduler.remove_all_jobs()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(day: int, hour: int, minute: int = 0, month: int = 8):
    return TZ.localize(datetime(2025, month, day, hour, minute))


def _fires(days, hour, minute=0):
    return [_dt(d, hour, minute) for d in sorted(days)]


def _trigger_fire_times(trigger) -> List[datetime]:
    fires = []
    previous = None
    now = WINDOW_START
    for _ in range(MAX_FIRES):
        nxt = trigger.get_next_fire_time(previous, now)
        if nxt is None or nxt >= WINDOW_END:
            break
        fires.append(nxt.astimezone(TZ))
        previous, now = nxt, nxt
    return fires


def _croniter_fire_times(expr: str, day_or: bool) -> List[datetime]:
    it = croniter(expr, WINDOW_START - timedelta(microseconds=1), day_or=day_or)
    fires = []
    for _ in range(MAX_FIRES):
        nxt = it.get_next(datetime)
        if nxt >= WINDOW_END:
            break
        fires.append(nxt.astimezone(TZ))
    return fires


def _fmt(fires) -> str:
    return "[" + ", ".join(f.strftime("%a %Y-%m-%d %H:%M") for f in fires) + "]"


def _scheduled_fires(service, job_id: int) -> List[datetime]:
    sched_job = service.scheduler.get_job(f"job_{job_id}")
    assert sched_job is not None, f"job_{job_id} was not registered in the scheduler"
    return _trigger_fire_times(sched_job.trigger)


def _transient_job(job_id: int, expr: str, day_or=None) -> ScheduledJob:
    job = ScheduledJob(
        id=job_id,
        name=f"day-or-{job_id}",
        cron_expression=expr,
        timezone_name=TZ_NAME,
        is_cron_expression=True,
    )
    if day_or is not None:
        setattr(job, "day_or", day_or)
    return job


def _assert_fires(actual, expected, expr, mode):
    assert actual == expected, (
        f"GSSD-311: cron '{expr}' ({mode}) fired on the wrong dates in Aug 2025.\n"
        f"  expected: {_fmt(expected)}\n"
        f"  actual:   {_fmt(actual)}"
    )


# ---------------------------------------------------------------------------
# Trigger-level behaviour
# ---------------------------------------------------------------------------

_SUNDAYS = [3, 10, 17, 24, 31]
_THURSDAYS = [7, 14, 21, 28]
_WEDNESDAYS = [6, 13, 20, 27]
_SATURDAYS = [2, 9, 16, 23, 30]

# (id, expression, day_or flag, expected fire times)
_OR_AND_CASES = [
    ("or-sunday-or-21st", "0 16 21 8 0", True, _fires({21, *_SUNDAYS}, 16)),
    ("or-1st-or-thursday", "0 16 1 8 4", True, _fires({1, *_THURSDAYS}, 16)),
    ("or-21st-is-thursday", "0 16 21 8 4", True, _fires(_THURSDAYS, 16)),
    ("or-original-report-20th-or-thursday", "50 11 20 8 4", True, _fires({20, *_THURSDAYS}, 11, 50)),
    ("or-wednesdays-incl-20th", "0 12 20 8 3", True, _fires(_WEDNESDAYS, 12)),
    ("and-sunday-and-21st", "0 16 21 8 0", False, []),
    ("and-thursday-and-21st", "0 16 21 8 4", False, _fires({21}, 16)),
    ("and-first-saturday-of-month", "0 9 1-7 * 6", False, _fires({2}, 9)),
]


@pytest.mark.parametrize(
    "expr,day_or,expected",
    [pytest.param(e, f, x, id=i) for i, e, f, x in _OR_AND_CASES],
)
def test_day_or_trigger_fire_times(service, expr, day_or, expected):
    explicit_flag = None if day_or else False
    job = _transient_job(1, expr, day_or=explicit_flag)
    service.create_job(job)

    actual = _scheduled_fires(service, job.id)
    mode = "default OR" if explicit_flag is None else "day_or=False (AND)"

    _assert_fires(_croniter_fire_times(expr, day_or), expected, expr, "croniter reference")
    _assert_fires(actual, expected, expr, mode)


_WILDCARD_CASES = [
    ("every-day-of-august", "0 16 * 8 *", _fires(range(1, 32), 16)),
    ("21st-only", "0 16 21 8 *", _fires({21}, 16)),
    ("thursdays-only", "0 16 * 8 4", _fires(_THURSDAYS, 16)),
    ("every-30-minutes-on-21st", "*/30 16 21 8 *", [_dt(21, 16, 0), _dt(21, 16, 30)]),
]


@pytest.mark.parametrize("flag", [None, True, False], ids=["default", "day_or-true", "day_or-false"])
@pytest.mark.parametrize(
    "expr,expected",
    [pytest.param(e, x, id=i) for i, e, x in _WILDCARD_CASES],
)
def test_wildcard_expressions_unaffected_by_day_or(service, expr, expected, flag):
    job = _transient_job(2, expr, day_or=flag)
    service.create_job(job)

    actual = _scheduled_fires(service, job.id)

    _assert_fires(_croniter_fire_times(expr, True), expected, expr, "croniter reference")
    _assert_fires(_croniter_fire_times(expr, False), expected, expr, "croniter reference AND")
    _assert_fires(actual, expected, expr, f"day_or={flag}")


# ---------------------------------------------------------------------------
# Plumbing: schemas and ORM model
# ---------------------------------------------------------------------------

def _job_create_kwargs(**overrides):
    data = dict(
        name="day-or-job",
        task_type=TaskType.PIPELINE_START,
        cron_expression="0 16 21 8 0",
        pipeline_id="pipeline-1",
        is_cron_expression=True,
    )
    data.update(overrides)
    return data


def test_job_create_accepts_day_or_default_true():
    assert "day_or" in JobCreate.model_fields, "GSSD-311: JobCreate has no 'day_or' field"
    assert JobCreate(**_job_create_kwargs()).day_or is True
    assert JobCreate(**_job_create_kwargs(day_or=False)).day_or is False


def test_job_update_day_or_optional_default_none():
    assert "day_or" in JobUpdate.model_fields, "GSSD-311: JobUpdate has no 'day_or' field"
    assert JobUpdate().day_or is None
    assert JobUpdate(day_or=None).day_or is None
    assert JobUpdate(day_or=False).day_or is False
    assert JobUpdate(day_or=True).day_or is True


def test_job_response_model_exposes_day_or():
    assert "day_or" in Job.model_fields, "GSSD-311: response model Job does not expose 'day_or'"


def test_scheduled_job_has_day_or_column():
    columns = {c.key: c for c in sqlalchemy.inspect(ScheduledJob).columns}
    assert "day_or" in columns, "GSSD-311: ScheduledJob has no mapped 'day_or' column"
    column = columns["day_or"]
    assert column.nullable is False, "GSSD-311: day_or column must be NOT NULL"
    assert column.default is not None and column.default.arg is True, (
        "GSSD-311: day_or column must default to True (cron standard OR)"
    )


# ---------------------------------------------------------------------------
# End to end through JobService with the real scheduler
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    db.add(Setting(key="timezone", value=TZ_NAME))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def job_service(db_session, service):
    from gluesync_scheduler.services.job_service import JobService

    svc = JobService(db_session)
    svc.scheduler_service = service
    return svc


def _create_via_job_service(job_service, **overrides):
    return job_service.create_job(JobCreate(**_job_create_kwargs(**overrides)))


def test_job_service_create_persists_day_or_false_and_uses_and(job_service, db_session, service):
    expr = "0 16 21 8 0"
    created = _create_via_job_service(job_service, cron_expression=expr, day_or=False)

    row = db_session.query(ScheduledJob).filter_by(id=created.id).one()
    assert getattr(row, "day_or", None) is False, (
        f"GSSD-311: day_or=False was not persisted (row.day_or={getattr(row, 'day_or', 'missing')!r})"
    )
    _assert_fires(_scheduled_fires(service, created.id), [], expr, "day_or=False (AND) via JobService")


def test_job_service_update_day_or_true_switches_to_or(job_service, db_session, service):
    expr = "0 16 21 8 0"
    created = _create_via_job_service(job_service, cron_expression=expr, day_or=False)

    job_service.update_job(created.id, JobUpdate(day_or=True))

    db_session.expire_all()
    row = db_session.query(ScheduledJob).filter_by(id=created.id).one()
    assert getattr(row, "day_or", None) is True, (
        f"GSSD-311: day_or=True was not persisted on update (row.day_or={getattr(row, 'day_or', 'missing')!r})"
    )
    expected = _fires({21, *_SUNDAYS}, 16)
    _assert_fires(_scheduled_fires(service, created.id), expected, expr, "day_or=True (OR) after update")
