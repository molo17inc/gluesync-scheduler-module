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

GSSD-1457 acceptance tests: catch-up of runs missed while Chronos was down.

At startup the latest fire time that passed since the job's persisted next_run
is replayed once when it is at most MISFIRE_GRACE_SECONDS old (a pipeline_exit_maintenance
job whatever its age), oldest first across jobs. The conftest stub of the scheduler module stays in sys.modules; the
real module is loaded from its file under a private name.
"""

import asyncio
import importlib.util
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.models.models import TaskType

SERVICE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "gluesync_scheduler", "services", "scheduler_service.py"
)

UTC = timezone.utc
GRACE = timedelta(seconds=3600)


@pytest.fixture(scope="module")
def module(tmp_path_factory):
    previous_log_dir = os.environ.get("CRON_LOG_DIR")
    os.environ["CRON_LOG_DIR"] = str(tmp_path_factory.mktemp("cron_logs"))
    loop = asyncio.new_event_loop()

    async def _import_real_module():
        # The module starts an AsyncIOScheduler at import time, which needs a running loop
        spec = importlib.util.spec_from_file_location("_real_scheduler_service_catchup", SERVICE_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    mod = loop.run_until_complete(_import_real_module())
    yield mod
    try:
        mod.SchedulerService.get_instance().scheduler.shutdown(wait=False)
    finally:
        mod.SchedulerService._instance = None
        loop.close()
        if previous_log_dir is None:
            os.environ.pop("CRON_LOG_DIR", None)
        else:
            os.environ["CRON_LOG_DIR"] = previous_log_dir


def make_job(job_id=1, cron="30 12 * * *", tz="UTC", next_run=None, last_run=None, name=None, day_or=True,
             task_type=None):
    return SimpleNamespace(
        id=job_id, name=name or f"job-{job_id}", cron_expression=cron, timezone_name=tz,
        day_or=day_or, next_run=next_run, last_run=last_run, task_type=task_type,
    )


def trigger_of(module, job):
    return module._trigger_for_job(job)[0]


def latest(module, job, now):
    return module.latest_missed_fire(trigger_of(module, job), job.next_run, job.last_run, now)


# ---------------------------------------------------------------------------
# latest_missed_fire
# ---------------------------------------------------------------------------

def test_grace_constant_is_shared(module):
    assert module.MISFIRE_GRACE_SECONDS == 3600
    scheduler = module.SchedulerService.get_instance().scheduler
    assert scheduler._job_defaults["misfire_grace_time"] == module.MISFIRE_GRACE_SECONDS


def test_no_miss_when_next_run_is_in_the_future(module):
    job = make_job(next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    assert latest(module, job, datetime(2026, 6, 10, 12, 0, tzinfo=UTC)) is None


def test_no_miss_without_persisted_next_run(module):
    job = make_job(next_run=None)
    assert latest(module, job, datetime(2026, 6, 10, 12, 40, tzinfo=UTC)) is None


def test_miss_within_window_is_replayed(module):
    job = make_job(next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    now = datetime(2026, 6, 10, 12, 40, tzinfo=UTC)
    assert latest(module, job, now) == datetime(2026, 6, 10, 12, 30, tzinfo=UTC)
    assert [(r.job_id, r.fire_time) for r in module.plan_missed_runs([job], now)] == [
        (1, datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    ]


def test_miss_older_than_the_window_is_skipped(module, caplog):
    job = make_job(job_id=7, name="nightly", next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    now = datetime(2026, 6, 10, 12, 30, tzinfo=UTC) + GRACE + timedelta(seconds=1)
    with caplog.at_level("WARNING"):
        assert module.plan_missed_runs([job], now) == []
    assert "job 7 'nightly'" in caplog.text and "2026-06-10T12:30:00" in caplog.text


def test_miss_exactly_at_the_window_edge_is_replayed(module):
    job = make_job(next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    now = datetime(2026, 6, 10, 12, 30, tzinfo=UTC) + GRACE
    assert len(module.plan_missed_runs([job], now)) == 1


def test_several_missed_fires_coalesce_to_the_latest(module):
    job = make_job(cron="*/10 * * * *", next_run=datetime(2026, 6, 10, 12, 0, tzinfo=UTC))
    now = datetime(2026, 6, 10, 12, 47, tzinfo=UTC)
    assert latest(module, job, now) == datetime(2026, 6, 10, 12, 40, tzinfo=UTC)
    plan = module.plan_missed_runs([job], now)
    assert [r.fire_time for r in plan] == [datetime(2026, 6, 10, 12, 40, tzinfo=UTC)]


def test_long_downtime_of_a_frequent_job_replays_the_latest_fire(module):
    job = make_job(cron="* * * * *", next_run=datetime(2026, 6, 1, 0, 0, tzinfo=UTC))
    now = datetime(2026, 6, 10, 12, 0, 30, tzinfo=UTC)
    assert latest(module, job, now) == datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    assert len(module.plan_missed_runs([job], now)) == 1


def test_last_run_at_or_after_the_missed_fire_means_it_already_ran(module):
    fire = datetime(2026, 6, 10, 12, 30, tzinfo=UTC)
    now = datetime(2026, 6, 10, 12, 40, tzinfo=UTC)
    ran_after = make_job(next_run=fire, last_run=fire + timedelta(seconds=2))
    ran_exactly = make_job(next_run=fire, last_run=fire)
    ran_before = make_job(next_run=fire, last_run=fire - timedelta(days=1))
    assert latest(module, ran_after, now) is None
    assert latest(module, ran_exactly, now) is None
    assert latest(module, ran_before, now) == fire


def test_naive_database_datetimes_are_utc(module):
    job = make_job(
        next_run=datetime(2026, 6, 10, 12, 30),          # naive, as SQLite returns them
        last_run=datetime(2026, 6, 9, 12, 30, 1),
    )
    now = datetime(2026, 6, 10, 12, 40, tzinfo=UTC)
    assert latest(module, job, now) == datetime(2026, 6, 10, 12, 30, tzinfo=UTC)
    assert latest(module, job, datetime(2026, 6, 10, 12, 40)) == datetime(2026, 6, 10, 12, 30, tzinfo=UTC)


def test_job_timezone_is_honoured(module):
    # 14:30 Europe/Rome in June is 12:30 UTC
    job = make_job(cron="30 14 * * *", tz="Europe/Rome", next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    assert latest(module, job, datetime(2026, 6, 10, 12, 40, tzinfo=UTC)) == datetime(2026, 6, 10, 12, 30, tzinfo=UTC)


def test_day_or_cron_is_honoured(module):
    # day 15 OR Monday: 2026-06-15 is a Monday, 2026-06-08 a Monday, 2026-06-10 neither
    job = make_job(cron="0 6 15 * 1", next_run=datetime(2026, 6, 1, 6, 0, tzinfo=UTC))
    assert latest(module, job, datetime(2026, 6, 10, 6, 30, tzinfo=UTC)) == datetime(2026, 6, 8, 6, 0, tzinfo=UTC)
    job_and = make_job(cron="0 6 15 * 1", day_or=False, next_run=datetime(2026, 6, 1, 6, 0, tzinfo=UTC))
    assert latest(module, job_and, datetime(2026, 6, 10, 6, 30, tzinfo=UTC)) is None


def test_dst_fall_back_europe_rome(module):
    # 2026-10-25 03:10 CET (02:10 UTC), right after the clocks went back at 03:00 CEST
    job = make_job(cron="10 3 * * 0", tz="Europe/Rome", next_run=datetime(2026, 10, 25, 2, 10, tzinfo=UTC))
    fire = datetime(2026, 10, 25, 2, 10, tzinfo=UTC)
    assert latest(module, job, datetime(2026, 10, 25, 2, 40, tzinfo=UTC)) == fire
    # the customer's restart at 06:03 CET (05:03 UTC) is beyond the window: skipped
    assert module.plan_missed_runs([job], datetime(2026, 10, 25, 5, 3, tzinfo=UTC)) == []
    # a daily 01:50 job stored with the CEST fire time (23:50 UTC on the 24th) ran before the freeze
    enter = make_job(2, "50 1 * * 0", "Europe/Rome",
                     next_run=datetime(2026, 10, 24, 23, 50, tzinfo=UTC),
                     last_run=datetime(2026, 10, 24, 23, 50, 1, tzinfo=UTC))
    assert latest(module, enter, datetime(2026, 10, 25, 2, 40, tzinfo=UTC)) is None


def test_dst_wall_clock_job_after_fall_back_is_not_duplicated(module):
    # Daily 02:30 Rome: next_run 02:30 CET on the 26th (01:30 UTC), restart 20 minutes later
    job = make_job(cron="30 2 * * *", tz="Europe/Rome", next_run=datetime(2026, 10, 26, 1, 30, tzinfo=UTC))
    assert latest(module, job, datetime(2026, 10, 26, 1, 50, tzinfo=UTC)) == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# plan_missed_runs
# ---------------------------------------------------------------------------

def test_plan_is_ordered_by_fire_time_across_jobs(module):
    now = datetime(2026, 6, 10, 22, 55, tzinfo=UTC)
    start = make_job(1, "30 22 * * *", name="start", next_run=datetime(2026, 6, 10, 22, 30, tzinfo=UTC))
    stop = make_job(2, "0 22 * * *", name="stop", next_run=datetime(2026, 6, 10, 22, 0, tzinfo=UTC))
    later = make_job(3, "45 22 * * *", name="later", next_run=datetime(2026, 6, 10, 22, 45, tzinfo=UTC))
    skipped = make_job(4, "0 6 * * *", name="old", next_run=datetime(2026, 6, 10, 6, 0, tzinfo=UTC))
    future = make_job(5, "0 23 * * *", name="future", next_run=datetime(2026, 6, 11, 23, 0, tzinfo=UTC))
    plan = module.plan_missed_runs([start, later, skipped, future, stop], now)
    # oldest fire first (22:00 stop, 22:30 start, 22:45), whatever the order of the jobs
    assert [r.job_id for r in plan] == [2, 1, 3]
    assert [r.job_name for r in plan] == ["stop", "start", "later"]


def test_plan_survives_a_broken_job(module, caplog):
    broken = make_job(1, "not a cron", next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    bad_tz = make_job(2, tz="Mars/Olympus", next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    good = make_job(3, next_run=datetime(2026, 6, 10, 12, 30, tzinfo=UTC))
    with caplog.at_level("ERROR"):
        plan = module.plan_missed_runs([broken, bad_tz, good], datetime(2026, 6, 10, 12, 40, tzinfo=UTC))
    assert [r.job_id for r in plan] == [3]
    assert "job 1" in caplog.text and "job 2" in caplog.text


def test_job_in_a_timezone_east_of_utc_missed_ten_minutes_ago_is_replayed(module):
    # next_run is stored as UTC (naive in SQLite): 03:10 CET on the fall-back day is 02:10 UTC.
    # Read as local wall time it would look 1 h later, "not yet due", and the miss would be skipped.
    job = make_job(cron="10 3 * * 0", tz="Europe/Rome", next_run=datetime(2026, 10, 25, 2, 10))
    now = datetime(2026, 10, 25, 2, 20, tzinfo=UTC)
    assert latest(module, job, now) == datetime(2026, 10, 25, 2, 10, tzinfo=UTC)
    assert [(r.job_id, r.fire_time) for r in module.plan_missed_runs([job], now)] == [
        (1, datetime(2026, 10, 25, 2, 10, tzinfo=UTC))
    ]
    summer = make_job(cron="30 14 * * *", tz="Europe/Rome", next_run=datetime(2026, 6, 10, 12, 30))
    assert len(module.plan_missed_runs([summer], datetime(2026, 6, 10, 12, 40, tzinfo=UTC))) == 1


# ---------------------------------------------------------------------------
# maintenance exit: replayed whatever its age
# ---------------------------------------------------------------------------

EXIT = TaskType.PIPELINE_EXIT_MAINTENANCE


def test_exit_maintenance_missed_three_hours_ago_is_replayed_and_others_are_skipped(module, caplog):
    # the customer case: 03:10 CET exit missed, Chronos restarted at 06:03 CET (05:03 UTC)
    now = datetime(2026, 10, 25, 5, 3, tzinfo=UTC)
    fire = datetime(2026, 10, 25, 2, 10, tzinfo=UTC)
    stored = datetime(2026, 10, 25, 2, 10)
    exit_job = make_job(1, "10 3 * * 0", "Europe/Rome", next_run=stored, name="exit", task_type=EXIT)
    start_job = make_job(2, "10 3 * * 0", "Europe/Rome", next_run=stored, name="start",
                         task_type=TaskType.PIPELINE_START)
    untyped = make_job(3, "10 3 * * 0", "Europe/Rome", next_run=stored, name="untyped")
    with caplog.at_level("INFO"):
        plan = module.plan_missed_runs([start_job, exit_job, untyped], now)
    assert [(r.job_id, r.fire_time) for r in plan] == [(1, fire)]
    assert "job 2 'start'" in caplog.text and "beyond the 3600s catch-up window" in caplog.text
    assert "Job 1 'exit'" in caplog.text and "catch-up window is waived" in caplog.text


@pytest.mark.parametrize("downtime", [timedelta(hours=3), timedelta(days=3)], ids=["3h", "3d"])
def test_weekly_exit_maintenance_missed_long_ago_is_found_and_replayed(module, downtime):
    # weekly job: no fire in the last hour, so the scan has to start from the persisted next_run
    fire = datetime(2026, 10, 25, 2, 10, tzinfo=UTC)
    job = make_job(1, "10 3 * * 0", "Europe/Rome", next_run=fire.replace(tzinfo=None), task_type=EXIT)
    now = fire + downtime
    assert latest(module, job, now) == fire
    assert [(r.job_id, r.fire_time) for r in module.plan_missed_runs([job], now)] == [(1, fire)]


def test_exit_maintenance_missed_for_weeks_replays_only_the_latest_fire(module):
    job = make_job(1, "10 3 * * 0", "Europe/Rome", next_run=datetime(2026, 10, 4, 1, 10), task_type=EXIT)
    now = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
    assert [r.fire_time for r in module.plan_missed_runs([job], now)] == [datetime(2026, 10, 25, 2, 10, tzinfo=UTC)]


def test_exit_maintenance_that_already_ran_is_not_replayed(module):
    fire = datetime(2026, 10, 25, 2, 10, tzinfo=UTC)
    job = make_job(1, "10 3 * * 0", "Europe/Rome", next_run=fire.replace(tzinfo=None), task_type=EXIT,
                   last_run=fire + timedelta(seconds=1))
    assert module.plan_missed_runs([job], fire + timedelta(hours=3)) == []


def test_exit_maintenance_with_a_future_next_run_is_not_replayed(module):
    job = make_job(1, "10 3 * * 0", "Europe/Rome", next_run=datetime(2026, 11, 1, 2, 10), task_type=EXIT)
    assert module.plan_missed_runs([job], datetime(2026, 10, 25, 5, 3, tzinfo=UTC)) == []


def test_waived_exit_replay_is_still_ordered_by_fire_time_with_the_others(module):
    now = datetime(2026, 10, 25, 5, 3, tzinfo=UTC)
    exit_job = make_job(1, "10 2 * * *", "UTC", next_run=datetime(2026, 10, 25, 2, 10), name="exit", task_type=EXIT)
    recent = make_job(2, "20 4 * * *", "UTC", next_run=datetime(2026, 10, 25, 4, 20), name="enter",
                      task_type=TaskType.PIPELINE_ENTER_MAINTENANCE)
    old_enter = make_job(3, "50 1 * * *", "UTC", next_run=datetime(2026, 10, 25, 1, 50), name="old enter",
                         task_type=TaskType.PIPELINE_ENTER_MAINTENANCE)
    plan = module.plan_missed_runs([recent, old_enter, exit_job], now)
    # oldest fire first; the 3 h old enter is beyond the window, the 3 h old exit is not
    assert [r.job_name for r in plan] == ["exit", "enter"]


# ---------------------------------------------------------------------------
# run_missed
# ---------------------------------------------------------------------------

async def test_run_missed_executes_in_order_and_survives_failures(module, monkeypatch):
    service = module.SchedulerService.get_instance()
    calls = []

    def fake_execute(job_id, job_name):
        calls.append((job_id, job_name, threading.current_thread() is threading.main_thread()))
        if job_id == 2:
            raise RuntimeError("boom")

    monkeypatch.setattr(service, "_execute_job", fake_execute)
    fire = datetime(2026, 6, 10, 12, 30, tzinfo=UTC)
    plan = [module.MissedRun(fire, 1, "a"), module.MissedRun(fire, 2, "b"), module.MissedRun(fire, 3, "c")]
    await service.run_missed(plan)
    assert [(c[0], c[1]) for c in calls] == [(1, "a"), (2, "b"), (3, "c")]
    assert not any(c[2] for c in calls), "replays must not block the event loop thread"
