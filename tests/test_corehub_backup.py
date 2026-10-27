#!/usr/bin/env python3
"""Tests for the corehub_backup Chronos job and the per-job timezone_name.

Core Hub backups are scheduled by Chronos: one ``corehub_backup`` job per Core
Hub POSTs ``/backup/scheduled-run`` with the chronos module JWT when it fires.
"""

import asyncio
import importlib.util
import os
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base, get_db
from gluesync_scheduler.models.corehub_backup import (
    CHRONOS_JOB_ID_HEADER,
    chain_event_payload,
    corehub_backup_internal_payload,
    hub_backup_body,
    hub_backup_headers,
)
from gluesync_scheduler.models.models import ExecutionMode, ScheduledJob, Setting, TaskType
from gluesync_scheduler.models.schemas import ChainedEventCreate, JobCreate, JobUpdate
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate
from gluesync_scheduler.services.chain_execution_service import (
    _event_payload,
    _task_type_to_action,
    _task_type_to_webhook_events,
    ExecutableEvent,
)
from gluesync_scheduler.services.job_service import _task_type_to_action as job_action

GLOBAL_TZ = "Asia/Tokyo"

# The job body Core Hub sends (backup-schedule contract, section 1).
CONTRACT_JOB = {
    "name": "Gluesync backup to Connect",
    "description": "Created by Core Hub: change the time in Core Hub > Settings > Backup.",
    "task_type": "corehub_backup",
    "pipeline_id": "",
    "is_cron_expression": True,
    "cron_expression": "30 2 * * *",
    "timezone_name": "Europe/Rome",
    "enabled": True,
}


# ---------------------------------------------------------------------------
# Schemas and wiring
# ---------------------------------------------------------------------------

def test_task_type_value():
    assert TaskType.CORE_HUB_BACKUP.value == "corehub_backup"
    assert TaskType("corehub_backup") is TaskType.CORE_HUB_BACKUP


def test_corehub_backup_does_not_require_a_pipeline():
    job = JobCreate(**CONTRACT_JOB)
    assert job.task_type == TaskType.CORE_HUB_BACKUP
    assert job.pipeline_id == ""
    assert job.timezone_name == "Europe/Rome"
    no_pipeline = {k: v for k, v in CONTRACT_JOB.items() if k != "pipeline_id"}
    assert JobCreate(**no_pipeline).pipeline_id == ""
    assert ChainedEventCreate(task_type=TaskType.CORE_HUB_BACKUP).pipeline_id == ""
    assert TriggerEventCreate(task_type=TaskType.CORE_HUB_BACKUP).pipeline_id == ""


def test_other_task_types_still_require_a_pipeline():
    with pytest.raises(ValidationError):
        JobCreate(**{**CONTRACT_JOB, "task_type": "pipeline_start", "pipeline_id": "  "})


def test_task_type_is_documented_in_the_schema():
    description = JobCreate.model_fields["task_type"].description
    assert "corehub_backup" in description


def test_corehub_backup_is_a_known_action():
    assert job_action(TaskType.CORE_HUB_BACKUP) == "corehub-backup"
    assert _task_type_to_action(TaskType.CORE_HUB_BACKUP) == "corehub-backup"
    assert _task_type_to_webhook_events(TaskType.CORE_HUB_BACKUP) == []


def test_internal_payload_carries_job_fired_at_and_manual():
    job = ScheduledJob(id=12, task_type=TaskType.CORE_HUB_BACKUP)
    fired = datetime(2026, 10, 26, 2, 30, 0, tzinfo=ZoneInfo("UTC"))
    assert corehub_backup_internal_payload(job, manual=True, fired_at=fired) == {
        "job_id": 12,
        "fired_at": "2026-10-26T02:30:00Z",
        "manual": True,
    }
    payload = corehub_backup_internal_payload(job)
    assert payload["manual"] is False
    assert payload["fired_at"].endswith("Z")


def _event(schedule_id=None):
    return ExecutableEvent(
        id=99, position=0, task_type=TaskType.CORE_HUB_BACKUP, pipeline_id="",
        entity_ids=None, group_ids=None, with_snapshot=False, snapshot_write_method="UPSERT",
        execution_mode=ExecutionMode.ASYNC, schedule_id=schedule_id,
    )


def test_chained_event_reports_the_parent_job_and_is_never_manual():
    payload = _event_payload(_event(schedule_id=12))
    assert payload["job_id"] == 12
    assert payload["manual"] is False
    assert chain_event_payload(_event())["job_id"] is None


def test_hub_body_and_header_match_the_contract():
    fired = datetime(2026, 10, 26, 2, 30, 0, tzinfo=ZoneInfo("UTC"))
    assert hub_backup_body(12, fired, False) == {
        "jobId": 12,
        "firedAt": "2026-10-26T02:30:00Z",
        "manual": False,
    }
    # A fire time in another zone is converted to UTC.
    rome = datetime(2026, 10, 26, 3, 30, 0, tzinfo=ZoneInfo("Europe/Rome"))
    assert hub_backup_body(12, rome, True)["firedAt"] == "2026-10-26T02:30:00Z"
    assert hub_backup_headers(12) == {CHRONOS_JOB_ID_HEADER: "12"}
    assert CHRONOS_JOB_ID_HEADER == "X-Chronos-Job-Id"
    assert hub_backup_headers(None) == {}


# ---------------------------------------------------------------------------
# timezone_name validation
# ---------------------------------------------------------------------------

def test_timezone_name_is_optional_and_blank_means_absent():
    without = {k: v for k, v in CONTRACT_JOB.items() if k != "timezone_name"}
    assert JobCreate(**without).timezone_name is None
    assert JobCreate(**{**CONTRACT_JOB, "timezone_name": "  "}).timezone_name is None
    assert JobUpdate().timezone_name is None
    assert JobUpdate(timezone_name="America/New_York").timezone_name == "America/New_York"


@pytest.mark.parametrize("bad", ["Mars/Olympus_Mons", "Europe/Rom", "../etc/passwd", "UTC+2"])
def test_unknown_timezone_name_is_rejected(bad):
    with pytest.raises(ValidationError):
        JobCreate(**{**CONTRACT_JOB, "timezone_name": bad})
    with pytest.raises(ValidationError):
        JobUpdate(timezone_name=bad)


# ---------------------------------------------------------------------------
# Core Hub call: outcome rules
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, status_code, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            import json as _json
            self.text = _json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
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
    sleeps = []

    def fake_post(url, json=None, headers=None, params=None, verify=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": dict(headers or {}), "timeout": timeout})
        if not responses:
            raise AssertionError("unexpected post")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("gluesync_scheduler.core.play_pause.requests.post", fake_post)
    monkeypatch.setattr("gluesync_scheduler.models.corehub_backup.time.sleep", sleeps.append)
    return client, calls, sleeps


FIRED = "2026-10-26T02:30:00Z"


def test_200_started_posts_the_contract(monkeypatch):
    client, calls, _ = _client(
        monkeypatch, [_Resp(200, {"started": True, "reason": "started", "nextAllowedAt": None})],
    )
    result = client.execute_corehub_backup(12, FIRED, False)
    assert result["success"] is True
    assert result["message"] == "started"
    assert result["started"] is True
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://corehub.test/backup/scheduled-run"
    assert call["json"] == {"jobId": 12, "firedAt": FIRED, "manual": False}
    assert call["headers"]["Authorization"] == "Bearer module-jwt"
    assert call["headers"]["X-Chronos-Job-Id"] == "12"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["timeout"] is not None


@pytest.mark.parametrize("reason", ["too_soon", "already_running", "disabled", "not_ready"])
def test_200_not_started_is_success_with_the_reason_as_message(monkeypatch, reason):
    client, calls, _ = _client(
        monkeypatch,
        [_Resp(200, {"started": False, "reason": reason, "nextAllowedAt": "2026-10-26T03:00:00Z"})],
    )
    result = client.execute_corehub_backup(12, FIRED, True)
    assert result["success"] is True
    assert result["retryable"] is False
    assert result["started"] is False
    assert result["message"] == reason
    assert result["next_allowed_at"] == "2026-10-26T03:00:00Z"
    assert calls[0]["json"]["manual"] is True
    assert len(calls) == 1


def test_5xx_is_retried_three_times_then_fails(monkeypatch):
    responses = [_Resp(503, {"message": "down"}) for _ in range(4)]
    client, calls, sleeps = _client(monkeypatch, responses)
    result = client.execute_corehub_backup(12, FIRED, False)
    assert result["success"] is False
    assert result["retryable"] is True
    assert len(calls) == 4
    assert sleeps == [2.0, 4.0]  # backoff factor 1: 0, 2, 4 s
    assert "HTTP 503" in result["message"]
    assert "after 4 attempts" in result["message"]


def test_5xx_then_200_succeeds(monkeypatch):
    client, calls, _ = _client(
        monkeypatch,
        [_Resp(502, text="bad gateway"), _Resp(200, {"started": True, "reason": "started"})],
    )
    result = client.execute_corehub_backup(12, FIRED, False)
    assert result["success"] is True
    assert len(calls) == 2


def test_network_error_is_retried(monkeypatch):
    import requests

    responses = [requests.ConnectionError("connection refused") for _ in range(4)]
    client, calls, _ = _client(monkeypatch, responses)
    result = client.execute_corehub_backup(12, FIRED, False)
    assert result["success"] is False
    assert result["retryable"] is True
    assert len(calls) == 4
    assert "unreachable" in result["message"]


@pytest.mark.parametrize("code", [400, 403, 404, 409, 429])
def test_4xx_fails_without_retry(monkeypatch, code):
    long_body = "No route " + "x" * 600
    client, calls, _ = _client(monkeypatch, [_Resp(code, text=long_body)])
    result = client.execute_corehub_backup(12, FIRED, False)
    assert result["success"] is False
    assert result["retryable"] is False
    assert result["status_code"] == code
    assert result["message"].startswith(f"Core Hub answered HTTP {code}: No route x")
    assert len(result["message"]) < 260  # body excerpt, not the whole body
    assert len(calls) == 1
    assert "disable_schedule" not in result


def test_401_refreshes_the_module_token_once(monkeypatch):
    client, calls, _ = _client(
        monkeypatch,
        [_Resp(401, {"message": "expired"}), _Resp(200, {"started": True, "reason": "started"})],
        tokens=[("expired-token", True), ("fresh-token", True)],
    )
    refreshed = {"n": 0}

    def _reinit():
        refreshed["n"] += 1

    monkeypatch.setattr(
        "gluesync_scheduler.core.play_pause.CoreHubClient._reinitialize_sdk_client",
        staticmethod(_reinit),
    )
    result = client.execute_corehub_backup(12, FIRED, False)
    assert result["success"] is True
    assert refreshed["n"] == 1
    assert len(calls) == 2
    assert calls[0]["headers"]["Authorization"] == "Bearer expired-token"
    assert calls[1]["headers"]["Authorization"] == "Bearer fresh-token"
    assert calls[1]["headers"]["X-Chronos-Job-Id"] == "12"
    assert calls[1]["json"] == {"jobId": 12, "firedAt": FIRED, "manual": False}


def test_401_twice_is_a_failed_run(monkeypatch):
    client, calls, _ = _client(
        monkeypatch,
        [_Resp(401, {"message": "expired"}), _Resp(401, {"message": "still expired"})],
        tokens=[("expired-token", True), ("fresh-token", True)],
    )
    result = client.execute_corehub_backup(12, FIRED, False)
    assert result["success"] is False
    assert result["retryable"] is False
    assert "HTTP 401" in result["message"]
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# REST API end to end: create / update / run now
# ---------------------------------------------------------------------------

@pytest.fixture
def db_engine():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    engine = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(Setting(key="timezone", value=GLOBAL_TZ))
    session.commit()
    session.close()
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


@pytest.fixture
def api_client(db_engine, monkeypatch):
    """TestClient on the test DB; the job runner's internal HTTP call is routed into the app."""
    from fastapi.testclient import TestClient

    import gluesync_scheduler.db.database as db_module
    from gluesync_scheduler.api import pipeline_router as pipeline_router_module
    from gluesync_scheduler.core.app import app
    from gluesync_scheduler.security import current_user, require_control, require_manage
    from gluesync_scheduler.security.corehub_introspect import CurrentUser
    from gluesync_scheduler.security.user_role import UserRole

    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    admin = CurrentUser(username="chronos", role=UserRole.EXTERNAL_MODULE)
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user] = lambda: admin
    app.dependency_overrides[require_control] = lambda: admin
    app.dependency_overrides[require_manage] = lambda: admin
    app.dependency_overrides[pipeline_router_module.verify_localhost] = lambda: None

    client = TestClient(app, raise_server_exceptions=False)

    def _internal_request(method, url, json=None, headers=None, timeout=None, verify=None, cert=None):
        path = url[url.index("/api/"):]
        return client.request(method, path, json=json, headers=headers)

    monkeypatch.setattr("gluesync_scheduler.services.job_service.requests.request", _internal_request)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def hub(monkeypatch):
    """Core Hub stand-in reached through the real CoreHubClient.fetch_core_hub."""
    from gluesync_scheduler.api import pipeline_router as pipeline_router_module
    from gluesync_scheduler.core.play_pause import PipelineManager

    state = {"responses": []}
    client, calls, sleeps = _client(monkeypatch, state["responses"])

    class _Manager(PipelineManager):
        def __init__(self):
            self.client = client
            self.job_identifier = None

    monkeypatch.setattr(pipeline_router_module, "PipelineManager", _Manager)
    state["calls"] = calls
    state["sleeps"] = sleeps
    return state


def _create(api_client, **overrides):
    resp = api_client.post("/api/jobs/", json={**CONTRACT_JOB, **overrides})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_returns_timezone_name_and_optional_pipeline(api_client):
    job = _create(api_client)
    assert job["task_type"] == "corehub_backup"
    assert job["pipeline_id"] == ""
    assert job["timezone_name"] == "Europe/Rome"
    assert job["enabled"] is True
    # The hour of next_run is not asserted: on SQLite Chronos stores it as local
    # wall-clock time and reads it back as UTC (pre-existing, see the trigger tests).
    assert datetime.fromisoformat(job["next_run"]).minute == 30

    fetched = api_client.get(f"/api/jobs/{job['id']}").json()
    assert fetched["timezone_name"] == "Europe/Rome"


def test_create_without_timezone_uses_the_global_setting(api_client):
    body = {k: v for k, v in CONTRACT_JOB.items() if k != "timezone_name"}
    resp = api_client.post("/api/jobs/", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["timezone_name"] == GLOBAL_TZ


def test_create_and_update_reject_unknown_timezone_with_422(api_client):
    resp = api_client.post("/api/jobs/", json={**CONTRACT_JOB, "timezone_name": "Europe/Atlantis"})
    assert resp.status_code == 422
    job = _create(api_client)
    resp = api_client.put(f"/api/jobs/{job['id']}", json={"timezone_name": "Europe/Atlantis"})
    assert resp.status_code == 422
    assert api_client.get(f"/api/jobs/{job['id']}").json()["timezone_name"] == "Europe/Rome"


def test_update_changes_time_and_timezone(api_client):
    job = _create(api_client)
    resp = api_client.put(
        f"/api/jobs/{job['id']}",
        json={"cron_expression": "15 * * * *", "timezone_name": "America/New_York"},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["cron_expression"] == "15 * * * *"
    assert updated["timezone_name"] == "America/New_York"
    assert updated["task_type"] == "corehub_backup"
    assert datetime.fromisoformat(updated["next_run"]).minute == 15


def test_update_timezone_only(api_client):
    job = _create(api_client)
    resp = api_client.put(f"/api/jobs/{job['id']}", json={"timezone_name": "America/New_York"})
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["timezone_name"] == "America/New_York"
    assert updated["cron_expression"] == "30 2 * * *"


def test_update_without_timezone_keeps_it_when_the_time_does_not_change(api_client):
    job = _create(api_client)
    resp = api_client.put(f"/api/jobs/{job['id']}", json={"name": "renamed", "enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone_name"] == "Europe/Rome"


def test_update_time_without_timezone_adopts_the_global_setting(api_client):
    """Behaviour before per-job timezones: a new schedule is stamped with the global setting."""
    job = _create(api_client)
    resp = api_client.put(f"/api/jobs/{job['id']}", json={"cron_expression": "45 4 * * *"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone_name"] == GLOBAL_TZ


def test_update_with_null_timezone_resets_to_the_global_setting(api_client):
    job = _create(api_client)
    resp = api_client.put(f"/api/jobs/{job['id']}", json={"timezone_name": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone_name"] == GLOBAL_TZ


def test_run_now_posts_manual_true(api_client, hub):
    job = _create(api_client)
    hub["responses"].append(_Resp(200, {"started": True, "reason": "started", "nextAllowedAt": None}))
    resp = api_client.post(f"/api/jobs/{job['id']}/run")
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["success"] is True
    assert result["message"] == "started"
    assert len(hub["calls"]) == 1
    call = hub["calls"][0]
    assert call["url"] == "http://corehub.test/backup/scheduled-run"
    assert call["headers"]["X-Chronos-Job-Id"] == str(job["id"])
    assert call["json"]["jobId"] == job["id"]
    assert call["json"]["manual"] is True
    assert call["json"]["firedAt"].endswith("Z")
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["last_successful_run"] is not None
    assert stored["last_error_message"] is None


def test_scheduled_fire_posts_manual_false(api_client, hub, db_engine):
    from gluesync_scheduler.services.job_service import JobService

    job = _create(api_client)
    hub["responses"].append(_Resp(200, {"started": True, "reason": "started"}))
    db = sessionmaker(bind=db_engine)()
    try:
        result = JobService(db).run_job(job["id"])  # what SchedulerService._execute_job calls
    finally:
        db.close()
    assert result["success"] is True
    assert hub["calls"][0]["json"]["manual"] is False


def test_run_now_not_started_is_a_successful_run(api_client, hub):
    job = _create(api_client)
    hub["responses"].append(
        _Resp(200, {"started": False, "reason": "too_soon", "nextAllowedAt": "2026-10-26T03:00:00Z"})
    )
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is True
    assert result["message"] == "too_soon"
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["last_successful_run"] is not None
    assert stored["last_error_message"] is None


def test_run_now_4xx_fails_and_leaves_the_job_enabled(api_client, hub):
    job = _create(api_client)
    hub["responses"].append(_Resp(404, text='{"error":"not_found","message":"No route /backup/scheduled-run"}'))
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is False
    assert result["message"].startswith("Core Hub answered HTTP 404: ")
    assert "No route /backup/scheduled-run" in result["message"]
    assert len(hub["calls"]) == 1
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["enabled"] is True
    assert stored["last_error_message"] == result["message"]
    assert stored["last_run_error_time"] is not None


def test_run_now_5xx_is_retried_then_fails(api_client, hub):
    job = _create(api_client)
    hub["responses"].extend(_Resp(500, text="boom") for _ in range(4))
    result = api_client.post(f"/api/jobs/{job['id']}/run").json()
    assert result["success"] is False
    assert len(hub["calls"]) == 4
    assert "HTTP 500" in result["message"]
    stored = api_client.get(f"/api/jobs/{job['id']}").json()
    assert stored["enabled"] is True
    assert "HTTP 500" in stored["last_error_message"]


def test_cli_job_runner_endpoint(monkeypatch):
    from gluesync_scheduler.cli.job_runner import execute_job

    posted = {}

    class _Http:
        status_code = 200
        text = '{"success": true, "message": "started"}'

    def fake_post(url, json=None, headers=None, verify=None, timeout=None):
        posted.update(url=url, json=json)
        return _Http()

    monkeypatch.setattr("gluesync_scheduler.cli.job_runner.requests.post", fake_post)
    job = ScheduledJob(
        id=12,
        name="backup",
        task_type=TaskType.CORE_HUB_BACKUP,
        cron_expression="30 2 * * *",
        pipeline_id="",
        enabled=True,
        command="pending",
        cron_job_identifier="gluesync_job_backup",
        snapshot_write_method="UPSERT",
    )
    assert execute_job(job) is True
    assert posted["url"].endswith("/api/pipelines/-/corehub-backup")
    assert posted["json"]["job_id"] == 12
    assert posted["json"]["manual"] is False


# ---------------------------------------------------------------------------
# The APScheduler trigger uses the job's timezone
# ---------------------------------------------------------------------------

SERVICE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "gluesync_scheduler", "services", "scheduler_service.py"
)


@pytest.fixture(scope="module")
def real_scheduler(tmp_path_factory):
    """The real SchedulerService, loaded under a private name (conftest stubs the module)."""
    previous_log_dir = os.environ.get("CRON_LOG_DIR")
    os.environ["CRON_LOG_DIR"] = str(tmp_path_factory.mktemp("cron_logs"))
    loop = asyncio.new_event_loop()

    async def _import_real_module():
        spec = importlib.util.spec_from_file_location("_real_scheduler_service_backup", SERVICE_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    module = loop.run_until_complete(_import_real_module())
    instance = module.SchedulerService.get_instance()
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


@pytest.fixture
def job_service(db_engine, real_scheduler):
    from gluesync_scheduler.services.job_service import JobService

    db = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    svc = JobService(db)
    svc.scheduler_service = real_scheduler
    yield svc
    real_scheduler.scheduler.remove_all_jobs()
    db.close()


def _trigger(real_scheduler, job_id):
    sched_job = real_scheduler.scheduler.get_job(f"job_{job_id}")
    assert sched_job is not None
    return sched_job.trigger


def _next_fire(trigger, tz_name):
    now = datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo(tz_name))
    return trigger.get_next_fire_time(None, now)


def test_trigger_uses_the_job_timezone(job_service, real_scheduler):
    created = job_service.create_job(JobCreate(**CONTRACT_JOB))
    trigger = _trigger(real_scheduler, created.id)
    assert str(trigger.timezone) == "Europe/Rome"
    fire = _next_fire(trigger, "Europe/Rome")
    assert (fire.hour, fire.minute) == (2, 30)
    assert fire.utcoffset() == ZoneInfo("Europe/Rome").utcoffset(fire.replace(tzinfo=None))


def test_trigger_without_timezone_uses_the_global_setting(job_service, real_scheduler):
    body = {k: v for k, v in CONTRACT_JOB.items() if k != "timezone_name"}
    created = job_service.create_job(JobCreate(**body))
    assert created.timezone_name == GLOBAL_TZ
    assert str(_trigger(real_scheduler, created.id).timezone) == GLOBAL_TZ


def test_trigger_follows_a_timezone_update(job_service, real_scheduler):
    created = job_service.create_job(JobCreate(**CONTRACT_JOB))
    job_service.update_job(created.id, JobUpdate(timezone_name="America/New_York"))
    trigger = _trigger(real_scheduler, created.id)
    assert str(trigger.timezone) == "America/New_York"
    fire = _next_fire(trigger, "America/New_York")
    assert (fire.hour, fire.minute) == (2, 30)
