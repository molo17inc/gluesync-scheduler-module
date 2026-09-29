#!/usr/bin/env python3
"""Tests for the visualize.refresh Chronos job."""

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.models.models import ScheduledJob, TaskType
from gluesync_scheduler.models.schemas import ChainedEventCreate, JobCreate
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate
from gluesync_scheduler.models.visualize_refresh import (
    hub_refresh_body,
    refresh_accepted,
)
from gluesync_scheduler.services.chain_execution_service import _task_type_to_action
from gluesync_scheduler.services.job_service import _task_type_to_action as job_action


def _job_create(**overrides):
    payload = {
        "name": "Nightly viz",
        "task_type": TaskType.VISUALIZE_REFRESH,
        "cron_expression": "0 3 * * *",
        "is_cron_expression": True,
        "viz_id": "viz-1",
        "visualize_parameters": {"country": "IT"},
    }
    payload.update(overrides)
    return JobCreate(**payload)


def test_visualize_refresh_does_not_require_a_pipeline():
    job = _job_create(pipeline_id="")
    assert job.task_type == TaskType.VISUALIZE_REFRESH
    assert job.pipeline_id == ""
    assert job.viz_id == "viz-1"
    assert job.visualize_parameters == {"country": "IT"}
    event = ChainedEventCreate(task_type=TaskType.VISUALIZE_REFRESH, viz_id="viz-1")
    assert event.pipeline_id == ""
    trigger = TriggerEventCreate(task_type=TaskType.VISUALIZE_REFRESH, viz_id="viz-1")
    assert trigger.pipeline_id == ""


def test_visualize_refresh_requires_viz_id():
    with pytest.raises(ValidationError):
        _job_create(viz_id="  ")


def test_visualize_refresh_is_a_known_action():
    assert job_action(TaskType.VISUALIZE_REFRESH) == "visualize-refresh"
    assert _task_type_to_action(TaskType.VISUALIZE_REFRESH) == "visualize-refresh"


def test_hub_body_includes_schedule_and_omits_it_when_absent():
    body = hub_refresh_body("viz-1", {"country": "IT"}, 42)
    assert body == {
        "task": "visualize.refresh",
        "vizId": "viz-1",
        "parameters": {"country": "IT"},
        "scheduleId": 42,
    }
    assert "taskType" not in body
    assert "payload" not in body
    manual = hub_refresh_body("viz-1", None, None)
    assert manual == {"task": "visualize.refresh", "vizId": "viz-1"}
    assert "scheduleId" not in manual
    assert "parameters" not in manual


def test_202_scheduled_true_and_false_are_success():
    assert refresh_accepted(202, {"scheduled": True, "trigger": "SCHEDULE"}) is True
    assert refresh_accepted(202, {"scheduled": False, "trigger": "SCHEDULE"}) is True


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _client(monkeypatch, responses, tokens=None):
    from gluesync_scheduler.core.play_pause import CoreHubClient

    client = object.__new__(CoreHubClient)
    client.base_url = "http://corehub.test"
    client.token = "module-jwt"
    client._initialized = True
    token_values = tokens or [("module-jwt", True)]

    def _token():
        if len(token_values) == 1:
            return token_values[0]
        return token_values.pop(0)

    monkeypatch.setattr(client, "_get_current_token", _token)
    monkeypatch.setattr(client, "_get_current_corehub_url", lambda: "http://corehub.test")
    monkeypatch.setattr(CoreHubClient, "_reinitialize_sdk_client", staticmethod(lambda: None))
    calls = []

    def fake_post(url, json=None, headers=None, params=None, verify=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        if not responses:
            raise AssertionError("unexpected post")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("gluesync_scheduler.core.play_pause.requests.post", fake_post)
    monkeypatch.setattr("gluesync_scheduler.models.visualize_refresh.time.sleep", lambda _delay: None)
    return client, calls


def test_post_path_method_and_body(monkeypatch):
    client, calls = _client(monkeypatch, [_Resp(202, {"scheduled": True, "visualizationId": "viz-1"})])
    result = client.execute_visualize_refresh("viz-1", {"country": "IT"}, 42)
    assert result["success"] is True
    assert result["retryable"] is False
    assert len(calls) == 1
    assert calls[0]["url"] == "http://corehub.test/visualize/refresh"
    assert calls[0]["json"]["task"] == "visualize.refresh"
    assert calls[0]["json"]["vizId"] == "viz-1"
    assert calls[0]["json"]["parameters"] == {"country": "IT"}
    assert calls[0]["json"]["scheduleId"] == 42
    assert calls[0]["headers"]["Authorization"] == "Bearer module-jwt"
    assert calls[0]["headers"]["Content-Type"] == "application/json"


def test_schedule_id_omitted_on_manual_run(monkeypatch):
    client, calls = _client(monkeypatch, [_Resp(202, {"scheduled": False})])
    result = client.execute_visualize_refresh("viz-1", {}, None)
    assert result["success"] is True
    assert "scheduleId" not in calls[0]["json"]
    assert "parameters" not in calls[0]["json"]


def test_202_scheduled_false_is_success_and_not_retried(monkeypatch):
    client, calls = _client(monkeypatch, [_Resp(202, {"scheduled": False})])
    result = client.execute_visualize_refresh("viz-1", {"country": "IT"}, 7)
    assert result["success"] is True
    assert len(calls) == 1


def test_400_and_403_fail_and_are_not_retried(monkeypatch):
    disabled = []
    monkeypatch.setattr(
        "gluesync_scheduler.models.visualize_refresh.disable_visualize_schedule",
        lambda schedule_id: disabled.append(schedule_id),
    )
    for code in (400, 403):
        client, calls = _client(monkeypatch, [_Resp(code, {"message": "no"})])
        result = client.execute_visualize_refresh("viz-1", {"country": "IT"}, 42)
        assert result["success"] is False
        assert result["retryable"] is False
        assert result["disable_schedule"] is False
        assert len(calls) == 1
    assert disabled == []


def test_404_disables_schedule_and_is_not_retried(monkeypatch):
    disabled = []
    monkeypatch.setattr(
        "gluesync_scheduler.models.visualize_refresh.disable_visualize_schedule",
        lambda schedule_id: disabled.append(schedule_id) or True,
    )
    client, calls = _client(monkeypatch, [_Resp(404, {"message": "missing"})])
    result = client.execute_visualize_refresh("viz-1", {"country": "IT"}, 42)
    assert result["success"] is False
    assert result["retryable"] is False
    assert result["disable_schedule"] is True
    assert disabled == [42]
    assert len(calls) == 1


def test_404_without_schedule_does_not_disable(monkeypatch):
    disabled = []
    monkeypatch.setattr(
        "gluesync_scheduler.models.visualize_refresh.disable_visualize_schedule",
        lambda schedule_id: disabled.append(schedule_id),
    )
    client, calls = _client(monkeypatch, [_Resp(404, {"message": "missing"})])
    result = client.execute_visualize_refresh("viz-1", None, None)
    assert result["disable_schedule"] is True
    assert disabled == []
    assert len(calls) == 1


def test_401_reauthenticates_once(monkeypatch):
    client, calls = _client(
        monkeypatch,
        [_Resp(401, {"message": "expired"}), _Resp(202, {"scheduled": True})],
        tokens=[("expired-token", True), ("fresh-token", True)],
    )
    refreshed = {"n": 0}

    def _reinit():
        refreshed["n"] += 1

    monkeypatch.setattr(
        "gluesync_scheduler.core.play_pause.CoreHubClient._reinitialize_sdk_client",
        staticmethod(_reinit),
    )
    result = client.execute_visualize_refresh("viz-1", {"country": "IT"}, 42)
    assert result["success"] is True
    assert refreshed["n"] == 1
    assert len(calls) == 2
    assert calls[0]["headers"]["Authorization"] == "Bearer expired-token"
    assert calls[1]["headers"]["Authorization"] == "Bearer fresh-token"
    assert calls[1]["json"]["vizId"] == "viz-1"


def test_5xx_is_retryable(monkeypatch):
    disabled = []
    monkeypatch.setattr(
        "gluesync_scheduler.models.visualize_refresh.disable_visualize_schedule",
        lambda schedule_id: disabled.append(schedule_id),
    )
    responses = [_Resp(503, {"message": "down"}) for _ in range(4)]
    client, calls = _client(monkeypatch, responses)
    result = client.execute_visualize_refresh("viz-1", {"country": "IT"}, 42)
    assert result["success"] is False
    assert result["retryable"] is True
    assert result["disable_schedule"] is False
    assert len(calls) == 4
    assert disabled == []


def test_network_error_is_retryable(monkeypatch):
    import requests

    responses = [requests.ConnectionError("reset") for _ in range(4)]
    client, calls = _client(monkeypatch, responses)
    result = client.execute_visualize_refresh("viz-1", None, None)
    assert result["success"] is False
    assert result["retryable"] is True
    assert len(calls) == 4


def test_job_runner_visualize_refresh_endpoint(monkeypatch):
    from gluesync_scheduler.cli.job_runner import execute_job

    posted = {}

    class _Http:
        status_code = 202
        text = '{"success": true}'

    def fake_post(url, json=None, headers=None, verify=None, timeout=None):
        posted.update(url=url, json=json)
        return _Http()

    monkeypatch.setattr("gluesync_scheduler.cli.job_runner.requests.post", fake_post)
    job = ScheduledJob(
        id=42,
        name="viz",
        task_type=TaskType.VISUALIZE_REFRESH,
        cron_expression="0 3 * * *",
        pipeline_id="",
        viz_id="viz-9",
        visualize_parameters='{"country": "IT"}',
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_viz",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith("/api/pipelines/-/visualize-refresh")
    assert posted["json"]["viz_id"] == "viz-9"
    assert posted["json"]["parameters"] == {"country": "IT"}
    assert posted["json"]["schedule_id"] == 42
