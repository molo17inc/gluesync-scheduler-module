#!/usr/bin/env python3
"""Tests for Chronos entity_validate scheduling and CoreHub Validator dispatch."""

import os
import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base
from gluesync_scheduler.models.entity_validation import (
    describe_differences,
    entity_validation_http_payload,
    require_entity_validation_fields,
    total_differences,
    validation_http_timeout_seconds,
)
from gluesync_scheduler.models.models import ScheduledJob, TaskType
from gluesync_scheduler.models.schemas import ChainedEventCreate, JobCreate, JobUpdate
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate
from gluesync_scheduler.services.chain_execution_service import (
    ExecutableEvent,
    ExecutionMode,
    _event_payload,
    _task_type_to_action,
    _task_type_to_webhook_events,
)
from gluesync_scheduler.services.job_service import JobService
from gluesync_scheduler.services.job_service import _task_type_to_action as job_action
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def _job_create(**overrides):
    data = dict(
        name="validate-job",
        task_type=TaskType.ENTITY_VALIDATE,
        cron_expression="0 7 * * *",
        pipeline_id="pipe-1",
        entity_ids=["ent-1"],
        validation_reconcile=True,
        enabled=True,
        is_cron_expression=True,
    )
    data.update(overrides)
    return JobCreate(**data)


def _run(status, **counts):
    run = {
        "jobId": "job-1",
        "status": status,
        "sourceRowsScanned": 10,
        "targetRowsScanned": 10,
        "missingInSourceCount": 0,
        "missingInTargetCount": 0,
        "rowMismatchesCount": 0,
        "schemaDifferencesCount": 0,
    }
    run.update(counts)
    return run


# ---------------------------------------------------------------------------
# Schema / persistence
# ---------------------------------------------------------------------------

def test_create_entity_validate_job_persists_reconcile_flag(db_session):
    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-validate"
        svc = JobService(db_session)
        created = svc.create_job(_job_create())

    assert created.task_type == TaskType.ENTITY_VALIDATE
    assert created.validation_reconcile is True
    stored = db_session.query(ScheduledJob).filter_by(id=created.id).first()
    assert stored.validation_reconcile is True
    assert stored.entity_ids == '["ent-1"]'


def test_update_entity_validate_job_toggles_reconcile(db_session):
    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-validate"
        svc = JobService(db_session)
        created = svc.create_job(_job_create())
        updated = svc.update_job(created.id, JobUpdate(validation_reconcile=False))

    assert updated.validation_reconcile is False


def test_entity_validate_requires_entity_ids():
    with pytest.raises(ValidationError):
        _job_create(entity_ids=None)
    with pytest.raises(ValueError):
        require_entity_validation_fields(TaskType.ENTITY_VALIDATE, "[]")
    require_entity_validation_fields(TaskType.ENTITY_SNAPSHOT, None)


def test_reconcile_flag_defaults_false_and_coerces():
    job = _job_create(validation_reconcile=None)
    assert job.validation_reconcile is False
    event = ChainedEventCreate(
        task_type=TaskType.ENTITY_VALIDATE,
        pipeline_id="pipe-1",
        entity_ids=["ent-1"],
        validation_reconcile=1,
    )
    assert event.validation_reconcile is True
    trigger = TriggerEventCreate(task_type=TaskType.ENTITY_VALIDATE, pipeline_id="pipe-1", entity_ids=["ent-1"])
    assert trigger.validation_reconcile is False


# ---------------------------------------------------------------------------
# Routing / payloads
# ---------------------------------------------------------------------------

def test_entity_validate_is_known_task_type():
    assert _task_type_to_action(TaskType.ENTITY_VALIDATE) == "validate"
    assert job_action(TaskType.ENTITY_VALIDATE) == "validate"
    assert _task_type_to_webhook_events(TaskType.ENTITY_VALIDATE) == []


def test_chain_event_payload_carries_entities_and_reconcile():
    event = ExecutableEvent(
        id=1,
        position=0,
        task_type=TaskType.ENTITY_VALIDATE,
        pipeline_id="pipe-1",
        entity_ids='["ent-1", "ent-2"]',
        group_ids=None,
        with_snapshot=False,
        snapshot_write_method="UPSERT",
        execution_mode=ExecutionMode.SYNC,
        validation_reconcile=True,
    )
    payload = _event_payload(event)
    assert payload == {"entity_ids": ["ent-1", "ent-2"], "validation_reconcile": True}


def test_http_payload_from_orm_job():
    job = ScheduledJob(
        name="v",
        task_type=TaskType.ENTITY_VALIDATE,
        cron_expression="0 * * * *",
        pipeline_id="pipe-1",
        entity_ids='["ent-1"]',
        validation_reconcile=False,
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_v",
        snapshot_write_method="UPSERT",
    )
    assert entity_validation_http_payload(job) == {"entity_ids": ["ent-1"], "validation_reconcile": False}


def test_validation_http_timeout_scales_with_entities(monkeypatch):
    monkeypatch.setenv("SCHEDULER_VALIDATION_TIMEOUT_SECONDS", "100")
    assert validation_http_timeout_seconds(1) == 130
    assert validation_http_timeout_seconds(3) == 330


def test_job_runner_entity_validate_is_not_unknown(monkeypatch):
    from gluesync_scheduler.cli.job_runner import execute_job

    posted = {}

    class _Resp:
        status_code = 200
        text = '{"success": true}'

    def fake_post(url, json=None, headers=None, verify=None, timeout=None):
        posted.update(url=url, json=json, timeout=timeout)
        return _Resp()

    monkeypatch.setattr("gluesync_scheduler.cli.job_runner.requests.post", fake_post)
    job = ScheduledJob(
        name="v",
        task_type=TaskType.ENTITY_VALIDATE,
        cron_expression="0 * * * *",
        pipeline_id="pipe-1",
        entity_ids='["ent-1"]',
        validation_reconcile=True,
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_v",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith("/api/pipelines/pipe-1/validate")
    assert posted["json"] == {"entity_ids": ["ent-1"], "validation_reconcile": True}
    assert posted["timeout"] >= 3600


def test_job_service_surfaces_router_detail_as_error(db_session):
    class _Resp:
        status_code = 500
        text = '{"detail": "Validation of entity ent-1 found 3 difference(s)"}'

        def json(self):
            return {"detail": "Validation of entity ent-1 found 3 difference(s)"}

    svc = JobService(db_session)
    ok, message, details = svc._interpret_execute_response(_Resp())
    assert ok is False
    assert message.endswith("Validation of entity ent-1 found 3 difference(s)")
    assert details["status_code"] == 500


# ---------------------------------------------------------------------------
# CoreHub client
# ---------------------------------------------------------------------------

def _client(fetch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fetch
    return client


def test_summary_helpers():
    run = _run("COMPLETED_WITH_WARNINGS", missingInTargetCount=2, rowMismatchesCount=1)
    assert total_differences(run) == 3
    assert describe_differences(run) == "2 only in source, 0 only in target, 1 mismatching rows, 0 schema differences"


def test_execute_entity_validation_success_without_differences(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    monkeypatch.setenv("SCHEDULER_VALIDATION_POLL_SECONDS", "0.01")
    calls = []

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        calls.append({"path": path, "method": method, "body": body})
        if method == "POST":
            return {"jobId": "job-1", "status": "PENDING", "startedAt": "now"}
        return _run("COMPLETED")

    ok, message, details = CoreHubClient.execute_entity_validation(_client(fake_fetch), "pipe-1", "ent-1", False)
    assert ok is True
    assert "match" in message
    assert calls[0] == {"path": "/data-comparison/runs", "method": "POST", "body": {"pipelineId": "pipe-1", "entityId": "ent-1"}}
    assert calls[1]["path"] == "/data-comparison/runs/job-1"
    assert details["totalDifferences"] == 0


def test_execute_entity_validation_fails_with_breakdown_when_reconcile_disabled(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    monkeypatch.setenv("SCHEDULER_VALIDATION_POLL_SECONDS", "0.01")
    calls = []

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        calls.append({"path": path, "method": method})
        if method == "POST":
            return {"jobId": "job-1", "status": "PENDING"}
        return _run("COMPLETED_WITH_WARNINGS", missingInTargetCount=2, rowMismatchesCount=1)

    ok, message, details = CoreHubClient.execute_entity_validation(_client(fake_fetch), "pipe-1", "ent-1", False)
    assert ok is False
    assert "found 3 difference(s)" in message
    assert "2 only in source" in message
    assert "Reconciliation is disabled" in message
    assert details["rowMismatchesCount"] == 1
    assert not any(call["path"].endswith("/reconciliation") for call in calls)


def test_execute_entity_validation_reconciles_all_when_enabled(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    monkeypatch.setenv("SCHEDULER_VALIDATION_POLL_SECONDS", "0.01")
    calls = []

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        calls.append({"path": path, "method": method, "body": body})
        if method == "POST" and path.endswith("/reconciliation"):
            return {"jobId": "job-1", "insertedCount": 2, "deletedCount": 0, "updatedCount": 1, "skippedCount": 0}
        if method == "POST":
            return {"jobId": "job-1", "status": "PENDING"}
        return _run("COMPLETED_WITH_WARNINGS", missingInTargetCount=2, rowMismatchesCount=1)

    ok, message, details = CoreHubClient.execute_entity_validation(_client(fake_fetch), "pipe-1", "ent-1", True)
    assert ok is True
    assert "Reconciled all" in message
    reconcile_call = next(call for call in calls if call["path"].endswith("/reconciliation"))
    assert reconcile_call["body"] == {"reconcileAll": True}
    assert details["reconciliation"]["insertedCount"] == 2


def test_execute_entity_validation_reports_leftovers_after_reconcile(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    monkeypatch.setenv("SCHEDULER_VALIDATION_POLL_SECONDS", "0.01")

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        if method == "POST" and path.endswith("/reconciliation"):
            return {"jobId": "job-1", "insertedCount": 1, "deletedCount": 0, "updatedCount": 0, "skippedCount": 1}
        if method == "POST":
            return {"jobId": "job-1", "status": "PENDING"}
        return _run("COMPLETED_WITH_WARNINGS", missingInTargetCount=2)

    ok, message, _ = CoreHubClient.execute_entity_validation(_client(fake_fetch), "pipe-1", "ent-1", True)
    assert ok is False
    assert "1 could not be fixed" in message


def test_execute_entity_validation_polls_until_terminal(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    monkeypatch.setenv("SCHEDULER_VALIDATION_POLL_SECONDS", "0.01")
    statuses = iter(["PENDING", "RUNNING", "FAILED"])

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        if method == "POST":
            return {"jobId": "job-1", "status": "PENDING"}
        return _run(next(statuses), errorMessage="boom")

    ok, message, details = CoreHubClient.execute_entity_validation(_client(fake_fetch), "pipe-1", "ent-1", True)
    assert ok is False
    assert "failed: boom" in message
    assert details["status"] == "FAILED"


def test_execute_entity_validation_times_out(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    monkeypatch.setenv("SCHEDULER_VALIDATION_POLL_SECONDS", "0.01")
    monkeypatch.setenv("SCHEDULER_VALIDATION_TIMEOUT_SECONDS", "1")
    client = _client(lambda *a, **k: {"jobId": "job-1", "status": "RUNNING"})
    # Shrink the deadline below one poll so the loop exits immediately.
    monkeypatch.setattr(
        "gluesync_scheduler.models.entity_validation.validation_timeout_seconds", lambda: 0.02
    )
    ok, message, _ = CoreHubClient.execute_entity_validation(client, "pipe-1", "ent-1", True)
    assert ok is False
    assert "did not finish in time" in message


def test_execute_entity_validation_trigger_error():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    client = _client(lambda *a, **k: {"status": "error", "status_code": 400, "message": "No comparison key"})
    ok, message, _ = CoreHubClient.execute_entity_validation(client, "pipe-1", "ent-1", False)
    assert ok is False
    assert "could not be started" in message
    assert "No comparison key" in message
