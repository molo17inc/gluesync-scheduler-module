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
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base
from gluesync_scheduler.models.models import ScheduledJob, TaskType, TriggerFlowEvent
from gluesync_scheduler.models.schemas import JobCreate
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate, TriggerFlowCreate
from gluesync_scheduler.services.chain_execution_service import _task_type_to_action
from gluesync_scheduler.services.job_service import JobService
from gluesync_scheduler.services.trigger_flow_service import TriggerFlowService
from gluesync_scheduler.security import CurrentUser, current_user
from gluesync_scheduler.security.user_role import UserRole


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
        name="qs-job",
        task_type=TaskType.QUERY_STUDIO,
        cron_expression="0 6 * * *",
        pipeline_id="pipe-1",
        agent_id="agent-9",
        query_sql="SELECT 1",
        enabled=True,
        is_cron_expression=True,
    )
    data.update(overrides)
    return JobCreate(**data)


def test_create_query_studio_job_stores_agent_and_sql(db_session):
    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-qs"
        svc = JobService(db_session)
        created = svc.create_job(_job_create())

    assert created.task_type == TaskType.QUERY_STUDIO
    assert created.agent_id == "agent-9"
    assert created.query_sql == "SELECT 1"
    assert created.query_read_only is True

    stored = db_session.query(ScheduledJob).filter_by(id=created.id).first()
    assert stored is not None
    assert stored.agent_id == "agent-9"
    assert stored.query_sql == "SELECT 1"
    assert stored.query_read_only is True


def test_job_create_coerces_null_query_read_only_for_entity_snapshot():
    """UI posts query_read_only=null for non-Query-Studio jobs; must coerce to True."""
    job = JobCreate(
        name="snap-job",
        task_type=TaskType.ENTITY_SNAPSHOT,
        cron_expression="0 0 * * *",
        pipeline_id="pipe-1",
        entity_ids=["entity-1"],
        query_read_only=None,
        enabled=True,
        is_cron_expression=True,
    )
    assert job.query_read_only is True


def test_job_create_coerces_null_query_read_only_for_query_studio():
    """QUERY_STUDIO create with query_read_only=None must coerce to True."""
    job = _job_create(query_read_only=None)
    assert job.query_read_only is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_id": None, "query_sql": "SELECT 1"},
        {"agent_id": "  ", "query_sql": "SELECT 1"},
        {"agent_id": "agent-9", "query_sql": None},
        {"agent_id": "agent-9", "query_sql": "   "},
    ],
)
def test_create_query_studio_job_rejects_missing_fields(overrides):
    with pytest.raises(ValidationError):
        _job_create(**overrides)


def test_trigger_flow_query_studio_event_is_persisted(db_session):
    svc = TriggerFlowService(db_session)
    data = TriggerFlowCreate(
        name="qs-flow",
        events=[
            TriggerEventCreate(
                task_type=TaskType.QUERY_STUDIO,
                pipeline_id="pipe-1",
                agent_id="agent-42",
                query_sql="SELECT name FROM users",
                execution_mode="async",
            )
        ],
    )
    flow, _token = svc.create_flow(data)
    evts = (
        db_session.query(TriggerFlowEvent)
        .filter_by(trigger_flow_id=flow.id)
        .all()
    )
    assert len(evts) == 1
    assert evts[0].task_type == TaskType.QUERY_STUDIO
    assert evts[0].agent_id == "agent-42"
    assert evts[0].query_sql == "SELECT name FROM users"
    assert evts[0].query_read_only is True


def test_execute_query_studio_posts_hub_path_and_body():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    captured = {}

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        captured.update(path=path, method=method, body=body, timeout=timeout)
        return {"status": "success", "status_code": 200, "operation_status": "COMPLETED"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_query_studio(client, "pipe-1", "agent-9", "SELECT 1")
    assert ok is True
    assert captured["path"] == "/query-studio/pipelines/pipe-1/agents/agent-9/execute"
    assert captured["method"] == "POST"
    assert captured["body"] == {"sql": "SELECT 1", "options": {"readOnly": True}}
    assert captured["timeout"] == 120


def test_execute_query_studio_sends_writable_options_when_user_opts_in():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    captured = {}

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        captured["body"] = body
        return {"status": "success", "status_code": 200, "operation_status": "COMPLETED"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_query_studio(
        client, "pipe-1", "agent-9", "UPDATE dbo.CUSTOMERS SET x = 1",
        query_read_only=False,
    )
    assert ok is True
    assert captured["body"]["sql"].startswith("UPDATE")
    assert captured["body"]["options"] == {"readOnly": False}


def test_execute_query_studio_treats_hub_status_error_as_failure():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        return {"status": "success", "status_code": 200, "operation_status": "ERROR"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_query_studio(client, "pipe-1", "agent-9", "SELECT 1")
    assert ok is False


def test_query_studio_is_known_task_type():
    assert _task_type_to_action(TaskType.QUERY_STUDIO) == "query-studio"


def test_job_runner_query_studio_is_not_unknown(monkeypatch):
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
        name="qs",
        task_type=TaskType.QUERY_STUDIO,
        cron_expression="0 * * * *",
        pipeline_id="pipe-1",
        agent_id="agent-9",
        query_sql="SELECT 1",
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_qs",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith("/api/pipelines/pipe-1/query-studio")
    assert posted["json"] == {"agent_id": "agent-9", "query_sql": "SELECT 1", "query_read_only": True}
    assert posted["timeout"] == 120


def test_list_query_studio_agents_forwards_to_hub():
    from fastapi.testclient import TestClient
    from gluesync_scheduler.core.app import app
    from gluesync_scheduler.core.play_pause import CoreHubClient

    hub_payload = [{"id": "agent-9", "name": "source-agent"}]
    captured = {}

    def fake_fetch(self, path, method="GET", body=None, params=None, timeout=None, raw=False):
        captured.update(path=path, method=method, raw=raw)
        return hub_payload

    async def fake_user():
        return CurrentUser(username="tester", role=UserRole.SUPER_ADMIN)

    app.dependency_overrides[current_user] = fake_user
    saved = CoreHubClient._instance
    CoreHubClient._instance = None
    try:
        with patch.object(CoreHubClient, "__init__", return_value=None), patch.object(
            CoreHubClient, "fetch_core_hub", fake_fetch
        ):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/query-studio/agents")
    finally:
        CoreHubClient._instance = saved
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == hub_payload
    assert captured["path"] == "/query-studio/agents"
    assert captured["method"] == "GET"


def test_create_query_studio_job_persists_saved_query_id(db_session):
    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-sq"
        svc = JobService(db_session)
        created = svc.create_job(
            _job_create(saved_query_id="sq-abc", query_sql="SELECT snapshot")
        )

    assert created.task_type == TaskType.QUERY_STUDIO
    assert created.saved_query_id == "sq-abc"
    assert created.query_sql == "SELECT snapshot"

    stored = db_session.query(ScheduledJob).filter_by(id=created.id).first()
    assert stored.saved_query_id == "sq-abc"
    assert stored.query_sql == "SELECT snapshot"


def test_create_query_studio_job_accepts_saved_query_id_without_sql():
    job = _job_create(query_sql=None, saved_query_id="sq-abc")
    assert job.task_type == TaskType.QUERY_STUDIO
    assert job.saved_query_id == "sq-abc"
    assert job.query_sql is None


def test_create_query_studio_job_rejects_missing_sql_and_saved_query_id():
    with pytest.raises(ValidationError):
        _job_create(query_sql=None, saved_query_id=None)


def test_query_studio_does_not_invent_extra_task_types():
    assert TaskType.QUERY_STUDIO.value == "query_studio"
    assert "QUERY_STUDIO_SAVED" not in TaskType.__members__
    assert "SAVED_QUERY" not in TaskType.__members__


def test_trigger_flow_persists_saved_query_id(db_session):
    svc = TriggerFlowService(db_session)
    data = TriggerFlowCreate(
        name="qs-saved-flow",
        events=[
            TriggerEventCreate(
                task_type=TaskType.QUERY_STUDIO,
                pipeline_id="pipe-1",
                agent_id="agent-42",
                query_sql="SELECT snapshot",
                saved_query_id="sq-flow",
                execution_mode="async",
            )
        ],
    )
    flow, _token = svc.create_flow(data)
    evts = (
        db_session.query(TriggerFlowEvent)
        .filter_by(trigger_flow_id=flow.id)
        .all()
    )
    assert len(evts) == 1
    assert evts[0].task_type == TaskType.QUERY_STUDIO
    assert evts[0].saved_query_id == "sq-flow"
    assert evts[0].query_sql == "SELECT snapshot"


def test_chained_query_studio_event_persists_saved_query_id(db_session):
    from gluesync_scheduler.models.models import ChainedJobEvent
    from gluesync_scheduler.models.schemas import ChainedEventCreate

    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-chain"
        svc = JobService(db_session)
        created = svc.create_job(
            _job_create(
                chained_events=[
                    ChainedEventCreate(
                        task_type=TaskType.QUERY_STUDIO,
                        pipeline_id="pipe-1",
                        agent_id="agent-9",
                        query_sql="SELECT chained",
                        saved_query_id="sq-chain",
                    )
                ]
            )
        )

    rows = (
        db_session.query(ChainedJobEvent)
        .filter_by(parent_job_id=created.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].task_type == TaskType.QUERY_STUDIO
    assert rows[0].saved_query_id == "sq-chain"
    assert created.chained_events[0].saved_query_id == "sq-chain"


def test_execute_query_studio_uses_snapshot_when_hub_fetch_fails():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    captured = []

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        captured.append({"path": path, "method": method, "body": body})
        if method == "GET":
            return {"status": "error", "status_code": 404, "message": "not found"}
        return {"status": "success", "status_code": 200, "operation_status": "COMPLETED"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_query_studio(
        client, "pipe-1", "agent-9", "SELECT snapshot", saved_query_id="sq-1"
    )
    assert ok is True
    get_calls = [c for c in captured if c["method"] == "GET"]
    post_calls = [c for c in captured if c["method"] == "POST"]
    assert get_calls[0]["path"] == "/query-studio/saved-queries/sq-1"
    assert post_calls[0]["path"] == "/query-studio/pipelines/pipe-1/agents/agent-9/execute"
    assert post_calls[0]["body"] == {"sql": "SELECT snapshot", "options": {"readOnly": True}}


def test_execute_query_studio_prefers_hub_sql_when_fetch_succeeds():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    captured = []

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        captured.append({"path": path, "method": method, "body": body})
        if method == "GET":
            return {"sql": "SELECT live FROM hub", "agentId": "agent-live"}
        return {"status": "success", "status_code": 200, "operation_status": "COMPLETED"}

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_query_studio(
        client, "pipe-1", "agent-9", "SELECT snapshot", saved_query_id="sq-1"
    )
    assert ok is True
    post_calls = [c for c in captured if c["method"] == "POST"]
    assert post_calls[0]["path"] == "/query-studio/pipelines/pipe-1/agents/agent-live/execute"
    assert post_calls[0]["body"] == {"sql": "SELECT live FROM hub", "options": {"readOnly": True}}


def test_execute_query_studio_fails_when_hub_and_snapshot_missing():
    from gluesync_scheduler.core.play_pause import CoreHubClient

    def fake_fetch(path, method="GET", body=None, params=None, timeout=None, raw=False):
        if method == "GET":
            return {"status": "error", "status_code": 403, "message": "forbidden"}
        raise AssertionError("execute should not be called when SQL is missing")

    client = CoreHubClient.__new__(CoreHubClient)
    client.fetch_core_hub = fake_fetch
    ok = CoreHubClient.execute_query_studio(
        client, "pipe-1", "agent-9", None, saved_query_id="sq-1"
    )
    assert ok is False


def test_job_runner_includes_saved_query_id_when_present(monkeypatch):
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
        name="qs",
        task_type=TaskType.QUERY_STUDIO,
        cron_expression="0 * * * *",
        pipeline_id="pipe-1",
        agent_id="agent-9",
        query_sql="SELECT snapshot",
        saved_query_id="sq-1",
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_qs_saved",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["json"] == {
        "agent_id": "agent-9",
        "query_sql": "SELECT snapshot",
        "saved_query_id": "sq-1",
        "query_read_only": True,
    }


def test_chain_event_payload_includes_saved_query_id():
    from gluesync_scheduler.models.models import ExecutionMode
    from gluesync_scheduler.services.chain_execution_service import ExecutableEvent, _event_payload

    event = ExecutableEvent(
        id=1,
        position=0,
        task_type=TaskType.QUERY_STUDIO,
        pipeline_id="pipe-1",
        entity_ids=None,
        group_ids=None,
        with_snapshot=False,
        snapshot_write_method="UPSERT",
        execution_mode=ExecutionMode.ASYNC,
        agent_id="agent-9",
        query_sql="SELECT snapshot",
        saved_query_id="sq-1",
    )
    assert _event_payload(event) == {
        "agent_id": "agent-9",
        "query_sql": "SELECT snapshot",
        "saved_query_id": "sq-1",
        "query_read_only": True,
    }


def test_create_query_studio_job_persists_query_read_only_false(db_session):
    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-writable"
        svc = JobService(db_session)
        created = svc.create_job(_job_create(query_read_only=False))

    assert created.query_read_only is False
    stored = db_session.query(ScheduledJob).filter_by(id=created.id).first()
    assert stored.query_read_only is False


def test_trigger_flow_persists_query_read_only_false(db_session):
    svc = TriggerFlowService(db_session)
    data = TriggerFlowCreate(
        name="qs-writable-flow",
        events=[
            TriggerEventCreate(
                task_type=TaskType.QUERY_STUDIO,
                pipeline_id="pipe-1",
                agent_id="agent-42",
                query_sql="UPDATE t SET x = 1",
                query_read_only=False,
                execution_mode="async",
            )
        ],
    )
    flow, _token = svc.create_flow(data)
    evts = (
        db_session.query(TriggerFlowEvent)
        .filter_by(trigger_flow_id=flow.id)
        .all()
    )
    assert len(evts) == 1
    assert evts[0].query_read_only is False


def test_chained_query_studio_event_persists_query_read_only_false(db_session):
    from gluesync_scheduler.models.models import ChainedJobEvent
    from gluesync_scheduler.models.schemas import ChainedEventCreate

    with patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc:
        mock_svc.create_job.return_value = "sched-id-chain-w"
        svc = JobService(db_session)
        created = svc.create_job(
            _job_create(
                chained_events=[
                    ChainedEventCreate(
                        task_type=TaskType.QUERY_STUDIO,
                        pipeline_id="pipe-1",
                        agent_id="agent-9",
                        query_sql="UPDATE t SET x = 1",
                        query_read_only=False,
                    )
                ]
            )
        )

    rows = (
        db_session.query(ChainedJobEvent)
        .filter_by(parent_job_id=created.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].query_read_only is False
    assert created.chained_events[0].query_read_only is False


def test_job_runner_includes_query_read_only_false(monkeypatch):
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
        name="qs",
        task_type=TaskType.QUERY_STUDIO,
        cron_expression="0 * * * *",
        pipeline_id="pipe-1",
        agent_id="agent-9",
        query_sql="UPDATE t SET x = 1",
        query_read_only=False,
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_qs_w",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["json"] == {
        "agent_id": "agent-9",
        "query_sql": "UPDATE t SET x = 1",
        "query_read_only": False,
    }


def test_chain_event_payload_includes_query_read_only_false():
    from gluesync_scheduler.models.models import ExecutionMode
    from gluesync_scheduler.services.chain_execution_service import ExecutableEvent, _event_payload

    event = ExecutableEvent(
        id=1,
        position=0,
        task_type=TaskType.QUERY_STUDIO,
        pipeline_id="pipe-1",
        entity_ids=None,
        group_ids=None,
        with_snapshot=False,
        snapshot_write_method="UPSERT",
        execution_mode=ExecutionMode.ASYNC,
        agent_id="agent-9",
        query_sql="UPDATE t SET x = 1",
        query_read_only=False,
    )
    assert _event_payload(event)["query_read_only"] is False
