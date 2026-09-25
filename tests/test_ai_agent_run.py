#!/usr/bin/env python3
"""Tests for Chronos ai_agent_run scheduling, interpolation, and CoreHub dispatch."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base
from gluesync_scheduler.models.ai_agent_run import (
    build_run_input,
    loop_guard_error,
    parse_capped_json_body,
    require_ai_agent_run_fields,
    template_tokens,
)
from gluesync_scheduler.models.models import ScheduledJob, TaskType
from gluesync_scheduler.models.schemas import JobCreate
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate
from gluesync_scheduler.services.chain_execution_service import (
    ExecutableEvent,
    ExecutionMode,
    _event_payload,
    _task_type_to_action,
    _task_type_to_webhook_events,
)
from gluesync_scheduler.services.job_service import JobService
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
        name="ai-job",
        task_type=TaskType.AI_AGENT_RUN,
        cron_expression="0 7 * * *",
        pipeline_id="",
        agent_alias="agent/daily-report",
        prompt_template="Summarize ticket {{data.ticketId}}",
        payload_allow_list=["data.ticketId"],
        enabled=True,
        is_cron_expression=True,
    )
    data.update(overrides)
    return JobCreate(**data)


def test_create_ai_agent_run_job_allows_empty_pipeline(db_session):
    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-ai"
        svc = JobService(db_session)
        created = svc.create_job(_job_create())

    assert created.task_type == TaskType.AI_AGENT_RUN
    assert created.agent_alias == "agent/daily-report"
    assert created.pipeline_id == ""
    stored = db_session.query(ScheduledJob).filter_by(id=created.id).first()
    assert stored.prompt_template.startswith("Summarize")
    assert "data.ticketId" in stored.payload_allow_list


def test_ai_agent_run_requires_alias():
    with pytest.raises(ValidationError):
        _job_create(agent_alias="")


def test_ai_agent_run_rejects_unknown_template_token():
    with pytest.raises(ValidationError):
        _job_create(prompt_template="Hello {{secret.token}}")


def test_ai_agent_run_rejects_nested_unsafe_path():
    with pytest.raises(ValueError):
        require_ai_agent_run_fields(
            TaskType.AI_AGENT_RUN,
            "agent/daily-report",
            "Hi {{data.__proto__}}",
            ["data.__proto__"],
        )


def test_template_tokens_remain_ascii_identifiers():
    assert template_tokens("{{data.ticket_123}} {{data.tïcket}}") == [
        "data.ticket_123"
    ]


def test_interpolation_copies_allow_listed_scalars_only():
    payload = {
        "data": {"ticketId": 123, "nested": {"x": 1}, "ok": True},
        "message": "ignored unless listed",
    }
    result = build_run_input(
        "Ticket {{data.ticketId}} flag={{data.ok}} skip={{data.nested}}",
        ["data.ticketId", "data.ok", "data.nested"],
        {"region": "eu-{{data.ticketId}}"},
        payload,
    )
    assert result["prompt"] == "Ticket 123 flag=true skip="
    assert result["fields"] == {"ticketId": 123, "ok": True}
    assert result["source"] == "chronos"
    assert result["region"] == "eu-123"


def test_loop_guard_refuses_ai_run_event_by_default():
    error = loop_guard_error(
        TaskType.AI_AGENT_RUN,
        "platform_event",
        {"type": "gluesync.ai.run.completed", "data": {"runId": "run-1"}},
        False,
    )
    assert error is not None


def test_loop_guard_caps_one_hop():
    error = loop_guard_error(
        TaskType.AI_AGENT_RUN,
        "platform_event",
        {
            "type": "gluesync.ai.run.completed",
            "data": {"runId": "run-1", "correlationId": "chronos:1:2:run-0"},
        },
        True,
    )
    assert error is not None


def test_loop_guard_allows_first_hop():
    error = loop_guard_error(
        TaskType.AI_AGENT_RUN,
        "platform_event",
        {"type": "gluesync.ai.run.completed", "data": {"runId": "run-1"}},
        True,
    )
    assert error is None


def test_parse_capped_json_body_rejects_oversize():
    with pytest.raises(ValueError):
        parse_capped_json_body(b"{" + b"a" * (65 * 1024) + b"}")


def test_ai_agent_run_is_known_task_type():
    assert _task_type_to_action(TaskType.AI_AGENT_RUN) == "ai-agent-run"
    assert _task_type_to_webhook_events(TaskType.AI_AGENT_RUN) == []


def test_chain_event_payload_includes_interpolated_input():
    event = ExecutableEvent(
        id=1,
        position=0,
        task_type=TaskType.AI_AGENT_RUN,
        pipeline_id="",
        entity_ids=None,
        group_ids=None,
        with_snapshot=False,
        snapshot_write_method="UPSERT",
        execution_mode=ExecutionMode.SYNC,
        agent_alias="agent/daily-report",
        prompt_template="Ticket {{data.ticketId}}",
        payload_allow_list=["data.ticketId"],
        resolved_input={"prompt": "Ticket 9", "fields": {"ticketId": "9"}, "source": "chronos"},
        correlation_id="chronos:1:1:abc",
    )
    payload = _event_payload(event)
    assert payload["agent_alias"] == "agent/daily-report"
    assert payload["wait"] is True
    assert payload["input"]["prompt"] == "Ticket 9"
    assert payload["correlation_id"] == "chronos:1:1:abc"


def test_execute_ai_agent_run_posts_then_polls_to_success():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    calls = []

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        calls.append({"path": path, "method": method, "body": body})
        if method == "POST":
            return {"id": "run-42", "status": "QUEUED"}
        return {"id": "run-42", "status": "SUCCEEDED"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_ai_agent_run(
        client,
        "agent/daily-report",
        {"prompt": "hi", "fields": {}, "source": "chronos"},
        agent_version=3,
        correlation_id="chronos:1:2:fire",
        wait=True,
    )
    assert ok is True
    assert calls[0]["path"].endswith("/agents/agent%2Fdaily-report/runs")
    assert calls[0]["body"]["agentVersion"] == 3
    assert calls[1]["path"] == "/api/ai/v1/runs/run-42"


def test_execute_ai_agent_run_polls_through_waiting_approval_without_resolving():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    calls = []

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        calls.append({"path": path, "method": method})
        if method == "POST":
            return {"id": "run-42", "status": "QUEUED"}
        return {"id": "run-42", "status": "WAITING_APPROVAL"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.AI_AGENT_RUN_TIMEOUT_SECONDS = 0.05
    client.AI_AGENT_RUN_POLL_SECONDS = 0.01
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_ai_agent_run(
        client, "agent/daily-report", {"prompt": "hi", "source": "chronos"}, wait=True
    )
    assert ok is False
    assert any(call["method"] == "GET" for call in calls)
    assert not any("/approvals/" in call["path"] and call["method"] == "POST" for call in calls)


def test_execute_ai_agent_run_does_not_treat_ambiguous_as_success():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        if method == "POST":
            return {"id": "run-42", "status": "QUEUED"}
        return {"id": "run-42", "status": "AMBIGUOUS"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_ai_agent_run(
        client, "agent/daily-report", {"prompt": "hi"}, wait=True
    )
    assert ok is False


def test_job_runner_ai_agent_run_is_not_unknown(monkeypatch):
    from gluesync_scheduler.cli.job_runner import execute_job

    posted = {}

    class _Resp:
        status_code = 202
        text = '{"success": true}'

    def fake_post(url, json=None, headers=None, verify=None, timeout=None):
        posted.update(url=url, json=json, timeout=timeout)
        return _Resp()

    monkeypatch.setattr("gluesync_scheduler.cli.job_runner.requests.post", fake_post)
    job = ScheduledJob(
        name="ai",
        task_type=TaskType.AI_AGENT_RUN,
        cron_expression="0 * * * *",
        pipeline_id="",
        agent_alias="agent/daily-report",
        prompt_template="hello",
        payload_allow_list="[]",
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_ai",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith("/api/pipelines/-/ai-agent-run")
    assert posted["json"]["agent_alias"] == "agent/daily-report"


def test_trigger_event_schema_accepts_ai_agent_run_without_pipeline():
    event = TriggerEventCreate(
        task_type=TaskType.AI_AGENT_RUN,
        agent_alias="agent/daily-report",
        prompt_template="Ticket {{data.ticketId}}",
        payload_allow_list=["data.ticketId"],
    )
    assert event.pipeline_id == ""
    assert event.agent_alias == "agent/daily-report"
