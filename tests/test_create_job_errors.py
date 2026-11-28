#!/usr/bin/env python3
"""Rejected job creations answer 4xx and leave nothing behind (sandbox findings
create-job-client-errors-answer-500 and failed-create-leaves-job-row)."""

import os
import sys
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.api import router as jobs_router
from gluesync_scheduler.db.database import Base, get_db
from gluesync_scheduler.models.models import ScheduledJob
from gluesync_scheduler.security import CurrentUser, require_manage
from gluesync_scheduler.security.user_role import UserRole


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _db():
        db = session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_manage] = lambda: CurrentUser(username="t", role=UserRole.SUPER_ADMIN)
    with patch("gluesync_scheduler.services.job_service.scheduler_service") as sched:
        sched.create_job.return_value = "job_1"
        with TestClient(app) as client:
            yield client, session, sched


def _rows(session):
    db = session()
    try:
        return db.query(ScheduledJob).count()
    finally:
        db.close()


def _job(**over):
    body = {"name": "j", "task_type": "pipeline_stop", "cron_expression": "0 6 * * *", "pipeline_id": "p"}
    body.update(over)
    return body


def test_group_start_without_group_ids_is_400(env):
    client, session, _ = env
    r = client.post("/api/jobs/", json=_job(task_type="group_start"))
    assert r.status_code == 400, r.text
    assert "group_ids" in r.json()["detail"]
    assert _rows(session) == 0


def test_no_cron_and_no_schedule_is_400(env):
    client, session, _ = env
    body = _job()
    del body["cron_expression"]
    r = client.post("/api/jobs/", json=body)
    assert r.status_code == 400, r.text
    assert _rows(session) == 0


def test_invalid_cron_is_400_and_stores_nothing(env):
    client, session, sched = env
    r = client.post("/api/jobs/", json=_job(cron_expression="61 25 * * *"))
    assert r.status_code == 400, r.text
    assert _rows(session) == 0
    sched.create_job.assert_not_called()


def test_a_scheduling_failure_removes_the_row(env):
    client, session, sched = env
    try:
        raise ValueError("APScheduler says no")
    except ValueError as cause:
        error = RuntimeError("Error creating scheduled job: APScheduler says no")
        error.__cause__ = cause
    sched.create_job.side_effect = error
    r = client.post("/api/jobs/", json=_job())
    assert r.status_code == 400, r.text
    assert _rows(session) == 0


def test_an_unexpected_scheduling_failure_is_500_and_removes_the_row(env):
    client, session, sched = env
    sched.create_job.side_effect = RuntimeError("scheduler is down")
    r = client.post("/api/jobs/", json=_job())
    assert r.status_code == 500, r.text
    assert _rows(session) == 0


def test_a_valid_job_is_created(env):
    client, session, _ = env
    r = client.post("/api/jobs/", json=_job())
    assert r.status_code == 201, r.text
    assert _rows(session) == 1
