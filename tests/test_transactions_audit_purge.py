#!/usr/bin/env python3
"""Tests for the pipeline_audit_purge, pipeline_audit_group_start and pipeline_audit_group_stop Chronos tasks.

Chronos POSTs to Core Hub /pipelines/{id}/transactions-audit/{purge | groups/{groupId}/start | groups/{groupId}/stop}
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
from gluesync_scheduler.models.schemas import ChainedEventCreate, JobCreate, JobUpdate
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

PURGE = {
    "name": "Audit purge",
    "task_type": "pipeline_audit_purge",
    "pipeline_id": "pipeline-123",
    "is_cron_expression": True,
    "cron_expression": "0 3 * * *",
    "enabled": True,
}
GROUP = {**PURGE, "name": "Audit group on", "task_type": "pipeline_audit_group_start", "group_ids": ["g1"]}

GROUP_RESULT = {
    "pipelineId": "pipeline-123",
    "groupId": "g1",
    "groupName": "Orders",
    "entities": [
        {"entityId": "e1", "entityName": "A", "outcome": "STARTED"},
        {"entityId": "e2", "entityName": "B", "outcome": "ALREADY_RECORDING"},
        {"entityId": "e3", "entityName": "C", "outcome": "SKIPPED", "reason": "no CDC"},
    ],
    "status": {"pipelineId": "pipeline-123", "state": "RECORDING"},
}


def _purge_answer(rows, complete, batches=1):
    return {
        "pipelineId": "pipeline-123", "cutoff": "2026-11-01T00:00:00Z", "retentionDays": 30,
        "rowsDeleted": rows, "batches": batches, "complete": complete, "tables": [],
    }


# --- schemas and wiring ----------------------------------------------------

def test_task_type_values():
    assert TaskType("pipeline_audit_purge") is TaskType.PIPELINE_AUDIT_PURGE
    assert TaskType("pipeline_audit_group_start") is TaskType.PIPELINE_AUDIT_GROUP_START
    assert TaskType("pipeline_audit_group_stop") is TaskType.PIPELINE_AUDIT_GROUP_STOP


def test_purge_age_is_optional_and_bounded():
    assert JobCreate(**PURGE).audit_older_than_days is None
    assert JobCreate(**{**PURGE, "audit_older_than_days": 30}).audit_older_than_days == 30
    for bad in (0, -1, 36501):
        with pytest.raises(ValidationError):
            JobCreate(**{**PURGE, "audit_older_than_days": bad})
        with pytest.raises(ValidationError):
            JobUpdate(task_type="pipeline_audit_purge", audit_older_than_days=bad)
        with pytest.raises(ValidationError):
            JobUpdate(audit_older_than_days=bad)
    # not a purge task: the age is ignored, not validated
    assert JobCreate(**{**PURGE, "task_type": "pipeline_audit_stop", "audit_older_than_days": 0})
    with pytest.raises(ValidationError):
        JobCreate(**{**PURGE, "pipeline_id": " "})


@pytest.mark.parametrize("task_type", [
    "pipeline_audit_purge", "pipeline_audit_group_start", "pipeline_audit_group_stop",
])
def test_events_accept_the_new_types(task_type):
    assert ChainedEventCreate(task_type=task_type, pipeline_id="p").pipeline_id == "p"
    assert TriggerEventCreate(task_type=task_type, pipeline_id="p", audit_older_than_days=7).audit_older_than_days == 7
    with pytest.raises(ValidationError):
        ChainedEventCreate(task_type="pipeline_audit_purge", pipeline_id="p", audit_older_than_days=0)


def test_task_types_are_documented_in_the_schema():
    description = JobCreate.model_fields["task_type"].description
    for name in ("pipeline_audit_purge", "pipeline_audit_group_start", "pipeline_audit_group_stop"):
        assert name in description


def test_actions_and_chain_events():
    for task_type, action in [
        (TaskType.PIPELINE_AUDIT_PURGE, "audit-purge"),
        (TaskType.PIPELINE_AUDIT_GROUP_START, "audit-group-start"),
        (TaskType.PIPELINE_AUDIT_GROUP_STOP, "audit-group-stop"),
    ]:
        assert job_action(task_type) == action
        assert _task_type_to_action(task_type) == action
        assert _task_type_to_webhook_events(task_type) == []


def _event(task_type, **kw):
    base = dict(
        id=1, position=0, task_type=task_type, pipeline_id="p", entity_ids=None, group_ids=None,
        with_snapshot=False, snapshot_write_method="UPSERT", execution_mode=ExecutionMode.ASYNC, schedule_id=None,
    )
    return ExecutableEvent(**{**base, **kw})


def test_chained_event_payloads():
    purge = _event(TaskType.PIPELINE_AUDIT_PURGE, entity_ids='["e1"]', audit_older_than_days=14)
    assert _event_payload(purge) == {"audit_older_than_days": 14, "entity_ids": ["e1"]}
    assert _event_payload(_event(TaskType.PIPELINE_AUDIT_PURGE)) == {}
    group = _event(TaskType.PIPELINE_AUDIT_GROUP_STOP, group_ids='["g1","g2"]')
    assert _event_payload(group)["group_ids"] == ["g1", "g2"]


# --- body, path and message shapes -------------------------------------------

def test_hub_bodies_and_paths():
    assert audit.audit_purge_hub_body() == {"entityIds": []}
    assert audit.audit_purge_hub_body(30, ["e1", ""]) == {"olderThanDays": 30, "entityIds": ["e1"]}
    assert audit.audit_purge_hub_path("p1") == "/pipelines/p1/transactions-audit/purge"
    assert audit.audit_group_hub_body("group-start") == {"confirmStatements": True}
    assert audit.audit_group_hub_body("group-stop") == {}
    assert audit.audit_group_hub_path("group-start", "p1", "g 1") == "/pipelines/p1/transactions-audit/groups/g%201/start"
    assert audit.audit_group_hub_path("group-stop", "p1", "g1") == "/pipelines/p1/transactions-audit/groups/g1/stop"


def test_group_success_message():
    result = audit.classify_group_result("group-start", "g1", GROUP_RESULT)
    assert result["success"] is True
    assert result["message"] == (
        "Audit started for group Orders, 1 already recording, 1 skipped, 1 started, state RECORDING"
    )
    assert audit.classify_group_result("group-stop", "g1", "ok")["message"] == "Audit stopped for group g1"


def test_purge_http_timeout_outlives_the_rounds():
    assert audit.purge_http_timeout_seconds() > audit.PURGE_DEADLINE_SECONDS + audit.CORE_HUB_PURGE_TIMEOUT_SECONDS


# --- Core Hub calls ------------------------------------------------------------

def test_group_start_posts_the_contract_per_group(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(202, GROUP_RESULT), _Resp(202, GROUP_RESULT)])
    result = client.execute_audit_group_action("group-start", "pipeline-123", ["g1", "g2"])
    assert result["success"] is True
    assert [c["url"] for c in calls] == [
        "http://corehub.test/pipelines/pipeline-123/transactions-audit/groups/g1/start",
        "http://corehub.test/pipelines/pipeline-123/transactions-audit/groups/g2/start",
    ]
    assert calls[0]["json"] == {"confirmStatements": True}
    assert calls[0]["headers"]["Authorization"] == "Bearer module-jwt"
    assert calls[0]["timeout"] == 30


def test_group_stop_has_an_empty_body(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(200, GROUP_RESULT)])
    assert client.execute_audit_group_action("group-stop", "p", ["g1"])["success"] is True
    assert calls[0]["url"].endswith("/pipelines/p/transactions-audit/groups/g1/stop")
    assert calls[0]["json"] == {}


def test_group_failure_does_not_skip_the_other_groups(monkeypatch):
    body = {"message": "no such group", "code": "GROUP_NOT_FOUND"}
    client, calls, _ = _client(monkeypatch, [_Resp(404, text=json.dumps(body)), _Resp(202, GROUP_RESULT)])
    result = client.execute_audit_group_action("group-start", "p", ["gone", "g1"])
    assert result["success"] is False
    assert result["retryable"] is False
    assert "Group gone: Core Hub answered HTTP 404: no such group (GROUP_NOT_FOUND)" in result["message"]
    assert "Audit started for group Orders" in result["message"]
    assert len(calls) == 2


def test_group_needs_a_group_id(monkeypatch):
    client, calls, _ = _client(monkeypatch, [])
    result = client.execute_audit_group_action("group-start", "p", [])
    assert result["success"] is False and calls == []


def test_group_5xx_is_retried(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(500, text="boom"), _Resp(202, GROUP_RESULT)])
    assert client.execute_audit_group_action("group-start", "p", ["g1"])["success"] is True
    assert len(calls) == 2


def test_purge_single_round(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(200, _purge_answer(120, True))])
    result = client.execute_audit_purge("pipeline-123", 30, ["e1"])
    assert result["success"] is True
    assert (result["rows_deleted"], result["rounds"], result["complete"]) == (120, 1, True)
    assert calls[0]["url"] == "http://corehub.test/pipelines/pipeline-123/transactions-audit/purge"
    assert calls[0]["json"] == {"olderThanDays": 30, "entityIds": ["e1"]}
    assert calls[0]["timeout"] == audit.CORE_HUB_PURGE_TIMEOUT_SECONDS


def test_purge_without_age_leaves_it_to_core_hub(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(200, _purge_answer(0, True))])
    assert client.execute_audit_purge("p")["success"] is True
    assert calls[0]["json"] == {"entityIds": []}


def test_purge_calls_again_until_complete(monkeypatch, caplog):
    client, calls, _ = _client(
        monkeypatch,
        [_Resp(200, _purge_answer(1000, False)), _Resp(200, _purge_answer(500, False)), _Resp(200, _purge_answer(7, True))],
    )
    with caplog.at_level("INFO", logger=audit.logger.name):
        result = client.execute_audit_purge("p", 30)
    assert len(calls) == 3
    assert result["complete"] is True and result["rounds"] == 3 and result["rows_deleted"] == 1507
    assert sum("round=" in r.getMessage() for r in caplog.records) == 3
    assert "Purge complete after 3 round(s), 1507 rows deleted" == result["message"]


def test_purge_stops_at_the_round_cap(monkeypatch):
    responses = [_Resp(200, _purge_answer(10, False)) for _ in range(audit.PURGE_MAX_ROUNDS + 5)]
    client, calls, _ = _client(monkeypatch, responses)
    result = client.execute_audit_purge("p", 30)
    assert len(calls) == audit.PURGE_MAX_ROUNDS
    assert result["success"] is True and result["complete"] is False
    assert "incomplete" in result["message"] and "next run goes on" in result["message"]


def test_purge_stops_at_the_deadline():
    ticks = iter([0, 5000])
    answers = iter([_purge_answer(10, False), _purge_answer(10, False)])
    calls = []

    def post(path, body):
        calls.append(path)
        return next(answers)

    result = audit.post_audit_purge(post, "p", 30, deadline_seconds=100, clock=lambda: next(ticks))
    assert len(calls) == 1
    assert result["complete"] is False and result["success"] is True


def test_purge_without_progress_does_not_spin(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(200, _purge_answer(0, False)) for _ in range(3)])
    result = client.execute_audit_purge("p", 30)
    assert len(calls) == 1
    assert "no progress" in result["message"]


def test_purge_4xx_fails_with_the_hub_code(monkeypatch):
    body = {"message": "Nothing says how old", "code": "RETENTION_NOT_SET", "field": "olderThanDays"}
    client, calls, _ = _client(monkeypatch, [_Resp(409, text=json.dumps(body))])
    result = client.execute_audit_purge("p")
    assert result["success"] is False and result["retryable"] is False
    assert result["message"] == "Core Hub answered HTTP 409: Nothing says how old (RETENTION_NOT_SET) [field olderThanDays]"
    assert len(calls) == 1


def test_purge_5xx_is_retried_once_then_fails(monkeypatch):
    client, calls, _ = _client(monkeypatch, [_Resp(502, {"message": "store"}) for _ in range(2)])
    result = client.execute_audit_purge("p")
    assert result["success"] is False and result["retryable"] is True
    assert len(calls) == 2 and "after 2 attempts" in result["message"]


def test_purge_failure_in_a_later_round_reports_the_rows_already_deleted(monkeypatch):
    body = {"message": "A purge of this pipeline is already running", "code": "PURGE_IN_PROGRESS"}
    client, _, _ = _client(monkeypatch, [_Resp(200, _purge_answer(40, False)), _Resp(409, text=json.dumps(body))])
    result = client.execute_audit_purge("p")
    assert result["success"] is False
    assert "PURGE_IN_PROGRESS" in result["message"] and "40 rows deleted before it" in result["message"]


# --- REST API end to end ---------------------------------------------------

def _create(api_client, base=PURGE, **overrides):
    resp = api_client.post("/api/jobs/", json={**base, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_run_now_purge_repeats_within_the_run(api_client, hub):
    job = _create(api_client, audit_older_than_days=30, entity_ids=["e1"])
    assert job["audit_older_than_days"] == 30
    hub["responses"].extend([_Resp(200, _purge_answer(10, False)), _Resp(200, _purge_answer(5, True))])
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is True, result
    assert [c["json"] for c in hub["calls"]] == [{"olderThanDays": 30, "entityIds": ["e1"]}] * 2
    assert all(c["url"].endswith("/pipelines/pipeline-123/transactions-audit/purge") for c in hub["calls"])
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["last_successful_run"] is not None
    assert stored["audit_older_than_days"] == 30


def test_run_now_purge_without_age(api_client, hub):
    job = _create(api_client)
    hub["responses"].append(_Resp(200, _purge_answer(0, True)))
    assert api_client.post(f"/api/jobs/{job['id']}/run").json()["success"] is True
    assert hub["calls"][0]["json"] == {"entityIds": []}


def test_run_now_purge_4xx_fails_and_leaves_the_job_enabled(api_client, hub):
    job = _create(api_client)
    hub["responses"].append(_Resp(409, text=json.dumps({"message": "No ledger", "code": "NO_LEDGER"})))
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is False and "NO_LEDGER" in result["message"]
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["enabled"] is True and "HTTP 409" in stored["last_error_message"]


def test_update_purge_age(api_client):
    job = _create(api_client, audit_older_than_days=30)
    resp = api_client.put(f"/api/jobs/{job['id']}", json={"audit_older_than_days": 90})
    assert resp.status_code == 200, resp.text
    assert resp.json()["audit_older_than_days"] == 90
    assert api_client.put(f"/api/jobs/{job['id']}", json={"audit_older_than_days": 0}).status_code == 422
    assert api_client.get(f"/api/jobs/{job['id']}").json()["audit_older_than_days"] == 90


def test_run_now_group_start_and_stop(api_client, hub):
    job = _create(api_client, GROUP, group_ids=["g1", "g2"])
    hub["responses"].extend([_Resp(202, GROUP_RESULT), _Resp(202, GROUP_RESULT)])
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is True, result
    assert [c["url"].rsplit("transactions-audit/", 1)[1] for c in hub["calls"]] == ["groups/g1/start", "groups/g2/start"]
    assert hub["calls"][0]["json"] == {"confirmStatements": True}

    stop = _create(api_client, GROUP, name="Audit group off", task_type="pipeline_audit_group_stop")
    hub["calls"].clear()
    hub["responses"].append(_Resp(200, GROUP_RESULT))
    assert api_client.post(f"/api/jobs/{stop['id']}/run").json()["success"] is True
    assert hub["calls"][0]["url"].endswith("/transactions-audit/groups/g1/stop")


def test_group_tasks_require_group_ids(api_client):
    no_groups = {k: v for k, v in GROUP.items() if k != "group_ids"}
    # Same answer as group_start: the job service wraps its 400 into "Error creating job: 400: ..."
    created = api_client.post("/api/jobs/", json=no_groups)
    assert created.status_code in (400, 500)
    assert "requires group_ids" in created.json()["detail"]
    job = _create(api_client, GROUP)
    updated = api_client.put(f"/api/jobs/{job['id']}", json={"group_ids": []})
    assert updated.status_code in (400, 500)
    assert "requires group_ids" in updated.json()["detail"]


def test_group_run_4xx_fails_the_run(api_client, hub):
    job = _create(api_client, GROUP)
    hub["responses"].append(_Resp(404, text=json.dumps({"message": "gone", "code": "GROUP_NOT_FOUND"})))
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is False and "GROUP_NOT_FOUND" in result["message"]


def test_route_unexpected_exception_is_a_500(api_client, hub, monkeypatch):
    from gluesync_scheduler.api import pipeline_router

    async def failing(self, *args, **kwargs):
        raise RuntimeError("kaput")

    monkeypatch.setattr(pipeline_router.PipelineManager, "execute_audit_purge", failing)
    monkeypatch.setattr(pipeline_router.PipelineManager, "execute_audit_group_action", failing)
    for job in (_create(api_client), _create(api_client, GROUP)):
        result = api_client.post(f"/api/jobs/{job['id']}/run").json()
        assert result["success"] is False and "kaput" in result["message"]


@pytest.mark.parametrize("task_type,suffix", [
    (TaskType.PIPELINE_AUDIT_PURGE, "audit-purge"),
    (TaskType.PIPELINE_AUDIT_GROUP_START, "audit-group-start"),
    (TaskType.PIPELINE_AUDIT_GROUP_STOP, "audit-group-stop"),
])
def test_cli_job_runner_endpoint_and_payload(monkeypatch, task_type, suffix):
    from gluesync_scheduler.cli.job_runner import execute_job

    posted = {}

    class _Http:
        status_code = 200
        text = '{"success": true}'

    def fake_post(url, json=None, headers=None, verify=None, timeout=None):
        posted.update(url=url, json=json, timeout=timeout)
        return _Http()

    monkeypatch.setattr("gluesync_scheduler.cli.job_runner.requests.post", fake_post)
    job = ScheduledJob(
        id=5, name="a", task_type=task_type, cron_expression="0 8 * * *", pipeline_id="pipeline-123",
        entity_ids='["e1"]', group_ids='["g1"]', audit_older_than_days=14, enabled=True, command="pending",
        cron_job_identifier="gluesync_job_a", snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith(f"/api/pipelines/pipeline-123/{suffix}")
    if task_type == TaskType.PIPELINE_AUDIT_PURGE:
        assert posted["json"] == {"audit_older_than_days": 14, "entity_ids": ["e1"]}
        assert posted["timeout"] == audit.purge_http_timeout_seconds()
    else:
        assert posted["json"]["group_ids"] == ["g1"]


def test_migration_adds_the_column_once():
    from sqlalchemy import create_engine, text

    from migrations.migrate_add_audit_purge_fields import add_audit_purge_fields

    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE scheduled_jobs (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE chained_job_events (id INTEGER PRIMARY KEY)"))
        conn.commit()
    add_audit_purge_fields(engine)
    add_audit_purge_fields(engine)
    with engine.connect() as conn:
        for table in ("scheduled_jobs", "chained_job_events"):
            cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
            assert cols.count("audit_older_than_days") == 1
