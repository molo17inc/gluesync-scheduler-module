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
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base, get_db
from gluesync_scheduler.models.models import ExecutionMode, TaskType, TriggerFlow, TriggerFlowEvent
from gluesync_scheduler.models.trigger_schemas import TriggerEventCreate, TriggerFlowCreate, TriggerFlowUpdate
from gluesync_scheduler.services.trigger_flow_service import TriggerFlowService


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def db_engine():
    """File-based SQLite so multiple connections share the same DB state."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    # Enable FK constraints
    @sa_event.listens_for(engine, "connect")
    def _set_fk_pragma(dbapi_conn, _rec):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture(scope="function")
def db_session(db_engine):
    Session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="function")
def api_client(db_engine):
    """FastAPI TestClient wired to the file-based test DB.

    Patches db_module.SessionLocal so every code path (dependency injection,
    background tasks) uses the same test engine.
    """
    import gluesync_scheduler.db.database as db_module
    from gluesync_scheduler.core.app import app

    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    _orig_session_local = db_module.SessionLocal
    db_module.SessionLocal = TestSessionLocal

    # Also override the get_db dependency so injected sessions use the test engine
    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    client = TestClient(app, raise_server_exceptions=False)
    yield client

    app.dependency_overrides.clear()
    db_module.SessionLocal = _orig_session_local


def _make_create_data(
    name: str = "test-flow",
    enabled: bool = True,
    events=None,
) -> TriggerFlowCreate:
    if events is None:
        events = [
            TriggerEventCreate(
                task_type=TaskType.PIPELINE_STOP,
                pipeline_id="pipe-1",
                execution_mode="async",
            ),
            TriggerEventCreate(
                task_type=TaskType.PIPELINE_START,
                pipeline_id="pipe-1",
                execution_mode="async",
            ),
        ]
    return TriggerFlowCreate(name=name, enabled=enabled, events=events)


# ---------------------------------------------------------------------------
# Unit tests — TriggerFlowService (no HTTP layer)
# ---------------------------------------------------------------------------

class TestTriggerFlowServiceCRUD:
    def test_create_returns_token_and_persists(self, db_session):
        svc = TriggerFlowService(db_session)
        data = _make_create_data()
        flow, token = svc.create_flow(data)

        assert flow.id is not None
        assert isinstance(token, str) and len(token) > 20
        assert flow.name == "test-flow"
        assert flow.enabled is True

        # Token stored on the ORM object matches what was returned
        stored = db_session.query(TriggerFlow).filter_by(id=flow.id).first()
        assert stored.secret_token == token

        # Events persisted in the correct order
        evts = (
            db_session.query(TriggerFlowEvent)
            .filter_by(trigger_flow_id=flow.id)
            .order_by(TriggerFlowEvent.position)
            .all()
        )
        assert len(evts) == 2
        assert evts[0].task_type == TaskType.PIPELINE_STOP
        assert evts[1].task_type == TaskType.PIPELINE_START

    def test_get_flow_attaches_events(self, db_session):
        svc = TriggerFlowService(db_session)
        flow, _ = svc.create_flow(_make_create_data())

        fetched = svc.get_flow(flow.id)
        assert fetched is not None
        assert len(fetched.events) == 2  # type: ignore[attr-defined]

    def test_get_nonexistent_flow_returns_none(self, db_session):
        svc = TriggerFlowService(db_session)
        assert svc.get_flow(9999) is None

    def test_list_flows_pagination(self, db_session):
        svc = TriggerFlowService(db_session)
        for i in range(5):
            svc.create_flow(_make_create_data(name=f"flow-{i}"))

        flows, total = svc.list_flows(skip=0, limit=3)
        assert total == 5
        assert len(flows) == 3

        flows2, total2 = svc.list_flows(skip=3, limit=10)
        assert total2 == 5
        assert len(flows2) == 2

    def test_update_flow_name_and_description(self, db_session):
        svc = TriggerFlowService(db_session)
        flow, _ = svc.create_flow(_make_create_data())

        updated = svc.update_flow(flow.id, TriggerFlowUpdate(name="renamed", description="desc"))
        assert updated.name == "renamed"
        assert updated.description == "desc"

    def test_update_flow_replaces_events(self, db_session):
        svc = TriggerFlowService(db_session)
        flow, _ = svc.create_flow(_make_create_data())

        new_events = [
            TriggerEventCreate(
                task_type=TaskType.PIPELINE_SNAPSHOT,
                pipeline_id="pipe-2",
                execution_mode="sync",
            )
        ]
        updated = svc.update_flow(flow.id, TriggerFlowUpdate(events=new_events))
        assert len(updated.events) == 1  # type: ignore[attr-defined]
        assert updated.events[0].task_type == TaskType.PIPELINE_SNAPSHOT  # type: ignore[attr-defined]

    def test_toggle_enabled(self, db_session):
        svc = TriggerFlowService(db_session)
        flow, _ = svc.create_flow(_make_create_data())
        assert flow.enabled is True

        disabled = svc.toggle_enabled(flow.id, False)
        assert disabled.enabled is False

        re_enabled = svc.toggle_enabled(flow.id, True)
        assert re_enabled.enabled is True

    def test_delete_flow(self, db_session):
        svc = TriggerFlowService(db_session)
        flow, _ = svc.create_flow(_make_create_data())
        flow_id = flow.id

        assert svc.delete_flow(flow_id) is True
        assert svc.get_flow(flow_id) is None

        # Events must also be gone (CASCADE)
        evts = db_session.query(TriggerFlowEvent).filter_by(trigger_flow_id=flow_id).all()
        assert len(evts) == 0

    def test_delete_nonexistent_flow_returns_false(self, db_session):
        svc = TriggerFlowService(db_session)
        assert svc.delete_flow(9999) is False

    def test_regenerate_token_invalidates_old(self, db_session):
        svc = TriggerFlowService(db_session)
        flow, old_token = svc.create_flow(_make_create_data())

        result = svc.regenerate_token(flow.id)
        assert result is not None
        _, new_token = result

        assert new_token != old_token
        # Old token no longer verifies
        assert svc.verify_token(flow.id, old_token) is None
        # New token verifies
        assert svc.verify_token(flow.id, new_token) is not None

    def test_verify_token_wrong_token_returns_none(self, db_session):
        svc = TriggerFlowService(db_session)
        flow, _ = svc.create_flow(_make_create_data())
        assert svc.verify_token(flow.id, "wrong-token") is None

    def test_verify_token_wrong_id_returns_none(self, db_session):
        svc = TriggerFlowService(db_session)
        _, token = svc.create_flow(_make_create_data())
        assert svc.verify_token(9999, token) is None


# ---------------------------------------------------------------------------
# Integration tests — REST API layer
# ---------------------------------------------------------------------------

class TestTriggerFlowAPI:
    def test_create_and_get(self, api_client):
        payload = {
            "name": "api-flow",
            "enabled": True,
            "events": [
                {"task_type": "pipeline_stop", "pipeline_id": "p1", "execution_mode": "async"},
            ],
        }
        resp = api_client.post("/api/triggers/", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "api-flow"
        assert "secret_token" in body
        assert len(body["secret_token"]) > 20
        flow_id = body["id"]

        # GET should not expose the secret_token
        get_resp = api_client.get(f"/api/triggers/{flow_id}")
        assert get_resp.status_code == 200
        get_body = get_resp.json()
        assert "secret_token" not in get_body
        assert get_body["id"] == flow_id

    def test_list_flows(self, api_client):
        for i in range(3):
            api_client.post(
                "/api/triggers/",
                json={"name": f"f{i}", "events": [
                    {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
                ]},
            )
        resp = api_client.get("/api/triggers/")
        assert resp.status_code == 200
        assert resp.json()["total"] == 3

    def test_update_flow(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "before", "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]

        put_resp = api_client.put(f"/api/triggers/{fid}", json={"name": "after"})
        assert put_resp.status_code == 200
        assert put_resp.json()["name"] == "after"

    def test_toggle_status(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "toggle-test", "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]

        patch_resp = api_client.patch(f"/api/triggers/{fid}/status", json={"enabled": False})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["enabled"] is False

    def test_delete_flow(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "to-delete", "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]

        del_resp = api_client.delete(f"/api/triggers/{fid}")
        assert del_resp.status_code == 204

        get_resp = api_client.get(f"/api/triggers/{fid}")
        assert get_resp.status_code == 404

    def test_regenerate_token(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "regen", "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]
        old_token = create_resp.json()["secret_token"]

        regen_resp = api_client.post(f"/api/triggers/{fid}/regenerate-token")
        assert regen_resp.status_code == 200
        new_token = regen_resp.json()["secret_token"]
        assert new_token != old_token

    def test_get_nonexistent_returns_404(self, api_client):
        resp = api_client.get("/api/triggers/9999")
        assert resp.status_code == 404

    # ------------------------------------------------------------------
    # Fire endpoint
    # ------------------------------------------------------------------

    def test_fire_missing_token_returns_401(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "fire-test", "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]
        resp = api_client.post(f"/api/triggers/{fid}/fire")
        assert resp.status_code == 401

    def test_fire_wrong_token_returns_401(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "fire-test-2", "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]
        resp = api_client.post(
            f"/api/triggers/{fid}/fire",
            headers={"X-Trigger-Token": "completely-wrong"},
        )
        assert resp.status_code == 401

    def test_fire_disabled_flow_returns_403(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "disabled-flow", "enabled": True, "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]
        token = create_resp.json()["secret_token"]

        api_client.patch(f"/api/triggers/{fid}/status", json={"enabled": False})

        resp = api_client.post(
            f"/api/triggers/{fid}/fire",
            headers={"X-Trigger-Token": token},
        )
        assert resp.status_code == 403

    def test_fire_async_returns_202(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "async-fire", "events": [
                {"task_type": "pipeline_start", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]
        token = create_resp.json()["secret_token"]

        with patch(
            "gluesync_scheduler.api.trigger_router.asyncio.ensure_future",
            return_value=MagicMock(),
        ):
            resp = api_client.post(
                f"/api/triggers/{fid}/fire",
                headers={"X-Trigger-Token": token},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert body["trigger_flow_id"] == fid

    def test_fire_wait_true_success(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "wait-fire", "events": [
                {"task_type": "pipeline_stop", "pipeline_id": "p", "execution_mode": "async"}
            ]},
        )
        fid = create_resp.json()["id"]
        token = create_resp.json()["secret_token"]

        with patch(
            "gluesync_scheduler.api.trigger_router.TriggerFlowService.fire",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            resp = api_client.post(
                f"/api/triggers/{fid}/fire?wait=true",
                headers={"X-Trigger-Token": token},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_fire_wait_true_failure(self, api_client):
        create_resp = api_client.post(
            "/api/triggers/",
            json={"name": "wait-fail", "events": [
                {"task_type": "pipeline_stop", "pipeline_id": "p", "execution_mode": "sync"}
            ]},
        )
        fid = create_resp.json()["id"]
        token = create_resp.json()["secret_token"]

        with patch(
            "gluesync_scheduler.api.trigger_router.TriggerFlowService.fire",
            new_callable=AsyncMock,
            return_value=(False, "Chain stopped early"),
        ):
            resp = api_client.post(
                f"/api/triggers/{fid}/fire?wait=true",
                headers={"X-Trigger-Token": token},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    def test_fire_nonexistent_flow_returns_401(self, api_client):
        """Unknown flow ID returns 401 (not 404) to avoid ID enumeration."""
        resp = api_client.post(
            "/api/triggers/9999/fire",
            headers={"X-Trigger-Token": "any-token"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Unit test — ExecutableEvent refactor backward-compat
# ---------------------------------------------------------------------------

class TestExecutableEvent:
    def test_from_chained_projects_correctly(self, db_session):
        from gluesync_scheduler.models.models import ChainedJobEvent
        from gluesync_scheduler.services.chain_execution_service import ExecutableEvent

        chained = ChainedJobEvent(
            id=1,
            parent_job_id=10,
            position=0,
            task_type=TaskType.PIPELINE_START,
            pipeline_id="p1",
            entity_ids=None,
            group_ids=None,
            with_snapshot=False,
            snapshot_write_method="UPSERT",
            execution_mode=ExecutionMode.ASYNC,
        )
        exe = ExecutableEvent.from_chained(chained)
        assert exe.id == 1
        assert exe.position == 0
        assert exe.task_type == TaskType.PIPELINE_START
        assert exe.pipeline_id == "p1"
        assert exe.execution_mode == ExecutionMode.ASYNC
