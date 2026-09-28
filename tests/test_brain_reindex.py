#!/usr/bin/env python3
"""Tests for Chronos Enterprise brain reindex scheduling."""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.models.brain_reindex import (
    brain_job_message,
    brain_rebuild_settled,
    brain_reindex_http_timeout_seconds,
)
from gluesync_scheduler.models.models import ScheduledJob, TaskType
from gluesync_scheduler.models.schemas import ChainedEventCreate, JobCreate
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate
from gluesync_scheduler.services.chain_execution_service import (
    _task_type_to_action,
    _task_type_to_webhook_events,
)
from gluesync_scheduler.services.job_service import _task_type_to_action as job_action


def _job_create(**overrides):
    payload = {
        "name": "Nightly brain",
        "task_type": TaskType.BRAIN_REINDEX,
        "cron_expression": "0 3 * * *",
        "is_cron_expression": True,
    }
    payload.update(overrides)
    return JobCreate(**payload)


def test_brain_reindex_does_not_require_a_pipeline():
    job = _job_create()
    assert job.task_type == TaskType.BRAIN_REINDEX
    assert job.pipeline_id == ""
    event = ChainedEventCreate(task_type=TaskType.BRAIN_REINDEX)
    assert event.pipeline_id == ""
    trigger = TriggerEventCreate(task_type=TaskType.BRAIN_REINDEX)
    assert trigger.pipeline_id == ""


def test_other_tasks_still_require_a_pipeline():
    with pytest.raises(ValidationError):
        JobCreate(
            name="pause",
            task_type=TaskType.PIPELINE_STOP,
            cron_expression="0 * * * *",
            is_cron_expression=True,
            pipeline_id="",
        )


def test_brain_reindex_is_a_known_action():
    assert job_action(TaskType.BRAIN_REINDEX) == "brain-reindex"
    assert _task_type_to_action(TaskType.BRAIN_REINDEX) == "brain-reindex"
    assert _task_type_to_webhook_events(TaskType.BRAIN_REINDEX) == []


def test_previous_ready_row_is_not_this_run(monkeypatch):
    monkeypatch.delenv("SCHEDULER_BRAIN_REINDEX_TIMEOUT_SECONDS", raising=False)
    previous = {"status": "READY", "generation": "gen-old", "message": "Enterprise brain is ready.", "tablesSeen": 1, "edgesSeen": 0}
    assert brain_rebuild_settled(previous, saw_creating=False, before_generation="gen-old", before_known=True) is False
    settled = {"status": "READY", "generation": "gen-new", "tablesSeen": 4, "edgesSeen": 2}
    assert brain_rebuild_settled(settled, saw_creating=False, before_generation="gen-old", before_known=True) is True
    failed = {"status": "FAILED", "generation": "gen-old", "message": "catalog walk failed"}
    assert brain_rebuild_settled(failed, saw_creating=True, before_generation="gen-old", before_known=True) is True
    assert "catalog walk failed" == brain_job_message(failed)
    assert "4 tables, 2 edges" in brain_job_message(settled)
    assert brain_reindex_http_timeout_seconds() == 3630


def test_execute_brain_reindex_waits_for_the_new_generation(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    calls = {"n": 0}

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        calls["n"] += 1
        assert path == "/api/ai/v1/brain/job"
        if method == "GET" and calls["n"] == 1:
            return {"status": "READY", "generation": "gen-old", "tablesSeen": 1, "edgesSeen": 0}
        if method == "POST":
            return {"status": "CREATING", "message": "Creating Enterprise brain..."}
        if calls["n"] < 4:
            return {"status": "CREATING", "generation": "gen-new", "tablesSeen": 2, "edgesSeen": 0}
        return {"status": "READY", "generation": "gen-new", "tablesSeen": 9, "edgesSeen": 3, "message": "Enterprise brain is ready."}

    client = object.__new__(CoreHubClient)
    monkeypatch.setattr(client, "fetch_core_hub", fake_fetch)
    monkeypatch.setenv("SCHEDULER_BRAIN_REINDEX_POLL_SECONDS", "0.2")
    success, message, details = client.execute_brain_reindex()
    assert success is True
    assert "9 tables, 3 edges" in message
    assert details["generation"] == "gen-new"


def test_execute_brain_reindex_fails_with_the_brain_message(monkeypatch):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    phase = {"started": False}

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        if method == "POST":
            phase["started"] = True
            return {"status": "CREATING"}
        if not phase["started"]:
            return {"status": "IDLE", "generation": None, "tablesSeen": 0, "edgesSeen": 0}
        return {"status": "FAILED", "generation": "gen-new", "message": "agent discovery failed"}

    client = object.__new__(CoreHubClient)
    monkeypatch.setattr(client, "fetch_core_hub", fake_fetch)
    monkeypatch.setenv("SCHEDULER_BRAIN_REINDEX_POLL_SECONDS", "0.2")
    success, message, _details = client.execute_brain_reindex()
    assert success is False
    assert message == "agent discovery failed"


def test_job_runner_brain_reindex_endpoint(monkeypatch):
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
        name="brain",
        task_type=TaskType.BRAIN_REINDEX,
        cron_expression="0 3 * * *",
        pipeline_id="",
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_brain",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith("/api/pipelines/-/brain-reindex")
    assert posted["json"] == {}
    assert posted["timeout"] == 3630
