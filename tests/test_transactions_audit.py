#!/usr/bin/env python3
"""Tests for the pipeline_audit_start and pipeline_audit_stop Chronos tasks.

Chronos POSTs to Core Hub /pipelines/{id}/transactions-audit/{start|stop}
with the chronos module JWT when the job fires.
"""

import json
import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.models import transactions_audit as audit
from gluesync_scheduler.models.models import ExecutionMode, ScheduledJob, TaskType
from gluesync_scheduler.models.schemas import ChainedEventCreate, JobCreate
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate
from gluesync_scheduler.services.chain_execution_service import (
    ExecutableEvent,
    _event_payload,
    _task_type_to_action,
    _task_type_to_webhook_events,
)
from gluesync_scheduler.services.job_service import _task_type_to_action as job_action

from test_corehub_backup import (  # noqa: E402  (shared fakes)
    _Resp,
    _client,
    api_client,
    db_engine,
    hub,
)

JOB = {
    "name": "Audit on",
    "task_type": "pipeline_audit_start",
    "pipeline_id": "pipeline-123",
    "is_cron_expression": True,
    "cron_expression": "0 8 * * 1-5",
    "enabled": True,
}

STATUS = {"pipelineId": "pipeline-123", "state": "RECORDING", "recordingCount": 3, "totalCount": 3}


# --- schemas and wiring ----------------------------------------------------

def test_task_type_values():
    assert TaskType("pipeline_audit_start") is TaskType.PIPELINE_AUDIT_START
    assert TaskType("pipeline_audit_stop") is TaskType.PIPELINE_AUDIT_STOP


@pytest.mark.parametrize("task_type", ["pipeline_audit_start", "pipeline_audit_stop"])
def test_pipeline_is_required_entities_are_not(task_type):
    job = JobCreate(**{**JOB, "task_type": task_type})
    assert job.pipeline_id == "pipeline-123"
    with pytest.raises(ValidationError):
        JobCreate(**{**JOB, "task_type": task_type, "pipeline_id": " "})
    no_pipeline = {k: v for k, v in JOB.items() if k != "pipeline_id"}
    with pytest.raises(ValidationError):
        JobCreate(**{**no_pipeline, "task_type": task_type})
    assert ChainedEventCreate(task_type=task_type, pipeline_id="p").pipeline_id == "p"
    assert TriggerEventCreate(task_type=task_type, pipeline_id="p").pipeline_id == "p"


def test_task_types_are_documented_in_the_schema():
    description = JobCreate.model_fields["task_type"].description
    assert "pipeline_audit_start" in description
    assert "pipeline_audit_stop" in description


def test_actions_and_chain_events():
    assert job_action(TaskType.PIPELINE_AUDIT_START) == "audit-start"
    assert job_action(TaskType.PIPELINE_AUDIT_STOP) == "audit-stop"
    assert _task_type_to_action(TaskType.PIPELINE_AUDIT_START) == "audit-start"
    assert _task_type_to_action(TaskType.PIPELINE_AUDIT_STOP) == "audit-stop"
    assert _task_type_to_webhook_events(TaskType.PIPELINE_AUDIT_START) == []
    assert _task_type_to_webhook_events(TaskType.PIPELINE_AUDIT_STOP) == []


def test_chained_event_payload_passes_entity_ids():
    event = ExecutableEvent(
        id=1, position=0, task_type=TaskType.PIPELINE_AUDIT_STOP, pipeline_id="p",
        entity_ids='["e1","e2"]', group_ids=None, with_snapshot=False, snapshot_write_method="UPSERT",
        execution_mode=ExecutionMode.ASYNC, schedule_id=None,
    )
    assert _event_payload(event)["entity_ids"] == ["e1", "e2"]


def test_purge_extension_point_is_empty():
    assert audit.AUDIT_PURGE_TASK_TYPES_PLACEHOLDER == ()


# --- body and message shapes -----------------------------------------------

def test_hub_bodies_and_paths():
    assert audit.audit_hub_body("start", []) == {"entityIds": [], "confirmStatements": True}
    assert audit.audit_hub_body("start", '["e1"]') == {"entityIds": ["e1"], "confirmStatements": True}
    assert audit.audit_hub_body("stop", ["e1", "e2"]) == {"entityIds": ["e1", "e2"]}
    assert audit.audit_hub_body("stop", None) == {"entityIds": []}
    assert audit.audit_hub_path("start", "p1") == "/pipelines/p1/transactions-audit/start"
    assert audit.audit_hub_path("stop", "p1") == "/pipelines/p1/transactions-audit/stop"


def test_success_message_from_status():
    result = audit.classify_hub_result("start", STATUS)
    assert result["success"] is True
    assert result["message"] == "Audit started, state RECORDING, 3 of 3 entities recording"
    assert result["state"] == "RECORDING"
    stopped = audit.classify_hub_result("stop", {"state": "OFF", "recordingCount": 0, "totalCount": 3})
    assert stopped["message"] == "Audit stopped, state OFF, 0 of 3 entities recording"
    assert audit.classify_hub_result("stop", "ok")["message"] == "Audit stopped"
    assert audit.classify_hub_result("start", None)["retryable"] is True


# --- Core Hub call ---------------------------------------------------------

def test_start_posts_the_contract(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(202, STATUS)])
    result = client.execute_audit_action("start", "pipeline-123", ["e1"])
    assert result["success"] is True
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://corehub.test/pipelines/pipeline-123/transactions-audit/start"
    assert call["json"] == {"entityIds": ["e1"], "confirmStatements": True}
    assert call["headers"]["Authorization"] == "Bearer module-jwt"
    assert call["timeout"] == 30


def test_stop_posts_the_contract(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(200, {**STATUS, "state": "OFF", "recordingCount": 0})])
    result = client.execute_audit_action("stop", "pipeline-123", [])
    assert result["success"] is True
    assert calls[0]["url"].endswith("/pipelines/pipeline-123/transactions-audit/stop")
    assert calls[0]["json"] == {"entityIds": []}


def test_5xx_is_retried_twice_then_fails(monkeypatch):
    client, calls, sleeps = _client(monkeypatch, [_Resp(502, {"message": "store"}) for _ in range(3)])
    sleeps.clear()
    monkeypatch.setattr(audit.time, "sleep", sleeps.append)
    result = client.execute_audit_action("start", "p", [])
    assert result["success"] is False
    assert result["retryable"] is True
    assert len(calls) == 3
    assert sleeps == [2.0]
    assert "after 3 attempts" in result["message"]


def test_5xx_then_202_succeeds(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(500, text="boom"), _Resp(202, STATUS)])
    assert client.execute_audit_action("start", "p", [])["success"] is True
    assert len(calls) == 2


def test_network_error_is_retried(monkeypatch):
    import requests

    client, calls, _ = _client(monkeypatch, [requests.ConnectionError("refused") for _ in range(3)])
    result = client.execute_audit_action("stop", "p", [])
    assert result["success"] is False
    assert result["retryable"] is True
    assert len(calls) == 3
    assert "unreachable" in result["message"]


def test_4xx_uses_the_error_response_and_is_not_retried(monkeypatch):
    body = {"notificationId": -1, "message": "Not configured", "code": "NOT_CONFIGURED", "field": "store"}
    client, calls, _ = _client(monkeypatch, [_Resp(409, text=json.dumps(body))])
    result = client.execute_audit_action("start", "p", [])
    assert result["success"] is False
    assert result["retryable"] is False
    assert result["message"] == "Core Hub answered HTTP 409: Not configured (NOT_CONFIGURED) [field store]"
    assert len(calls) == 1


def test_4xx_with_plain_text_body(monkeypatch):
    client, _, _ = _client(monkeypatch, [_Resp(403, text="Your role (VIEWER) does not have permission")])
    result = client.execute_audit_action("start", "p", [])
    assert result["message"] == "Core Hub answered HTTP 403: Your role (VIEWER) does not have permission"


# --- REST API end to end ---------------------------------------------------

def _create(api_client, **overrides):
    resp = api_client.post("/api/jobs/", json={**JOB, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_run_now_start_with_entities(api_client, hub):
    job = _create(api_client, entity_ids=["e1", "e2"])
    hub["responses"].append(_Resp(202, STATUS))
    resp = api_client.post(f"/api/jobs/{job['id']}/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    call = hub["calls"][0]
    assert call["url"].endswith("/pipelines/pipeline-123/transactions-audit/start")
    assert call["json"] == {"entityIds": ["e1", "e2"], "confirmStatements": True}
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["last_successful_run"] is not None
    assert stored["last_error_message"] is None


def test_run_now_stop_without_entities(api_client, hub):
    job = _create(api_client, task_type="pipeline_audit_stop")
    hub["responses"].append(_Resp(200, {**STATUS, "state": "OFF"}))
    assert api_client.post(f"/api/jobs/{job['id']}/run").json()["success"] is True
    assert hub["calls"][0]["json"] == {"entityIds": []}
    assert hub["calls"][0]["url"].endswith("/transactions-audit/stop")


def test_run_now_4xx_fails_and_leaves_the_job_enabled(api_client, hub):
    job = _create(api_client)
    hub["responses"].append(_Resp(409, text=json.dumps({"message": "Not configured", "code": "NOT_CONFIGURED"})))
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is False
    assert "NOT_CONFIGURED" in result["message"]
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["enabled"] is True
    assert "HTTP 409" in stored["last_error_message"]


def test_run_now_5xx_is_retried_then_fails(api_client, hub):
    job = _create(api_client)
    hub["responses"].extend(_Resp(500, text="boom") for _ in range(3))
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is False
    assert len(hub["calls"]) == 3


@pytest.mark.parametrize("task_type,suffix", [
    (TaskType.PIPELINE_AUDIT_START, "audit-start"),
    (TaskType.PIPELINE_AUDIT_STOP, "audit-stop"),
])
def test_cli_job_runner_endpoint(monkeypatch, task_type, suffix):
    from gluesync_scheduler.cli.job_runner import execute_job

    posted = {}

    class _Http:
        status_code = 200
        text = '{"success": true}'

    def fake_post(url, json=None, headers=None, verify=None, timeout=None):
        posted.update(url=url, json=json)
        return _Http()

    monkeypatch.setattr("gluesync_scheduler.cli.job_runner.requests.post", fake_post)
    job = ScheduledJob(
        id=5, name="a", task_type=task_type, cron_expression="0 8 * * *", pipeline_id="pipeline-123",
        entity_ids='["e1"]', enabled=True, command="pending", cron_job_identifier="gluesync_job_a",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith(f"/api/pipelines/pipeline-123/{suffix}")
    assert posted["json"]["entity_ids"] == ["e1"]


# --- edge branches ---------------------------------------------------------

def test_entity_ids_tolerate_blank_and_bad_json():
    assert audit.audit_hub_body("stop", "not json") == {"entityIds": []}
    assert audit.audit_hub_body("stop", '{"a": 1}') == {"entityIds": []}
    assert audit.audit_hub_body("stop", ["e1", "", None]) == {"entityIds": ["e1"]}


def test_error_without_status_code_or_body():
    assert audit.classify_hub_result("start", {"status": "error", "status_code": 500, "message": ""})["message"] \
        == "Core Hub answered HTTP 500"
    result = audit.classify_hub_result("start", {"status": "error", "status_code": 500, "error": "exception", "message": "x"})
    assert result["message"] == "Core Hub unreachable: x"
    assert result["retryable"] is True
    assert audit.classify_hub_result("stop", {"status": "error", "status_code": "bad"})["retryable"] is True


def test_route_unexpected_exception_is_a_500(api_client, hub, monkeypatch):
    job = _create(api_client)

    from gluesync_scheduler.api import pipeline_router

    async def failing(self, action, pipeline_id, entity_ids=None):
        raise RuntimeError("kaput")

    monkeypatch.setattr(pipeline_router.PipelineManager, "execute_audit_action", failing)
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is False
    assert "kaput" in result["message"]
