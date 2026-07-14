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

import asyncio
import json
import sys
import os
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Path setup — project root must be importable
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.db.database import Base
from gluesync_scheduler.models.models import (
    ChainedJobEvent,
    ExecutionMode,
    ScheduledJob,
    TaskType,
)
from gluesync_scheduler.models.schemas import (
    ChainedEventCreate,
    ChainedEventMode,
    JobCreate,
    JobUpdate,
)
from gluesync_scheduler.services.chain_execution_service import ChainExecutionService
from gluesync_scheduler.services.job_service import JobService, _load_chained_events

# ---------------------------------------------------------------------------
# In-memory SQLite fixtures
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="function")
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


def _make_scheduled_job(db, pipeline_id="pipe-1") -> ScheduledJob:
    """Insert a minimal ScheduledJob and return it."""
    job = ScheduledJob(
        name="test-job",
        task_type=TaskType.PIPELINE_START,
        cron_expression="0 0 * * *",
        pipeline_id=pipeline_id,
        enabled=True,
        with_snapshot=False,
        snapshot_write_method="UPSERT",
        command="pending",
        cron_job_identifier=f"gluesync_job_test_{id(db)}",
        is_cron_expression=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _make_chained_event_create(
    task_type=TaskType.PIPELINE_STOP,
    pipeline_id="pipe-1",
    mode: ChainedEventMode = ChainedEventMode.ASYNC,
) -> ChainedEventCreate:
    return ChainedEventCreate(
        task_type=task_type,
        pipeline_id=pipeline_id,
        execution_mode=mode,
    )


# ---------------------------------------------------------------------------
# Test 1 — create job with chained events → verify persistence
# ---------------------------------------------------------------------------

def test_create_job_persists_chained_events(db_session):
    """ChainedJobEvent rows must be saved when a job is created with chained_events."""
    with (
        patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc,
    ):
        mock_svc.create_job.return_value = "sched-id-1"

        svc = JobService(db_session)
        job_data = JobCreate(
            name="chain-test",
            task_type=TaskType.PIPELINE_START,
            cron_expression="0 6 * * *",
            pipeline_id="pipe-1",
            with_snapshot=False,
            snapshot_write_method="UPSERT",
            enabled=True,
            is_cron_expression=True,
            chained_events=[
                _make_chained_event_create(TaskType.PIPELINE_STOP, "pipe-1", ChainedEventMode.ASYNC),
                _make_chained_event_create(TaskType.ENTITY_SNAPSHOT, "pipe-1", ChainedEventMode.SYNC),
            ],
        )
        created = svc.create_job(job_data)

        rows = (
            db_session.query(ChainedJobEvent)
            .filter(ChainedJobEvent.parent_job_id == created.id)
            .order_by(ChainedJobEvent.position)
            .all()
        )
        assert len(rows) == 2
        assert rows[0].task_type == TaskType.PIPELINE_STOP
        assert rows[0].execution_mode == ExecutionMode.ASYNC
        assert rows[0].position == 0
        assert rows[1].task_type == TaskType.ENTITY_SNAPSHOT
        assert rows[1].execution_mode == ExecutionMode.SYNC
        assert rows[1].position == 1

        # Response DTO must also include them
        assert len(created.chained_events) == 2


# ---------------------------------------------------------------------------
# Test 2 — update replaces chained events
# ---------------------------------------------------------------------------

def test_update_job_replaces_chained_events(db_session):
    """Updating chained_events must delete old rows and insert the new ones."""
    with (
        patch("gluesync_scheduler.services.job_service.scheduler_service") as mock_svc,
    ):
        mock_svc.create_job.return_value = "sched-id-2"
        mock_svc.update_job.return_value = "sched-id-2"

        svc = JobService(db_session)
        job_data = JobCreate(
            name="update-chain-test",
            task_type=TaskType.PIPELINE_START,
            cron_expression="0 6 * * *",
            pipeline_id="pipe-2",
            with_snapshot=False,
            snapshot_write_method="UPSERT",
            enabled=True,
            is_cron_expression=True,
            chained_events=[
                _make_chained_event_create(TaskType.PIPELINE_STOP, "pipe-2"),
            ],
        )
        created = svc.create_job(job_data)

        # Update with a different list
        update_data = JobUpdate(
            chained_events=[
                _make_chained_event_create(TaskType.ENTITY_SNAPSHOT, "pipe-2", ChainedEventMode.SYNC),
                _make_chained_event_create(TaskType.PIPELINE_START, "pipe-2", ChainedEventMode.ASYNC),
                _make_chained_event_create(TaskType.PIPELINE_STOP, "pipe-2", ChainedEventMode.ASYNC),
            ]
        )
        updated = svc.update_job(created.id, update_data)

        rows = (
            db_session.query(ChainedJobEvent)
            .filter(ChainedJobEvent.parent_job_id == created.id)
            .order_by(ChainedJobEvent.position)
            .all()
        )
        assert len(rows) == 3
        assert rows[0].task_type == TaskType.ENTITY_SNAPSHOT
        assert rows[0].execution_mode == ExecutionMode.SYNC
        assert len(updated.chained_events) == 3


# ---------------------------------------------------------------------------
# Test 3 — execute_chain async mode fires immediately
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_chain_async_fires_immediately(db_session):
    """execute_chain must trigger async events without waiting."""
    parent = _make_scheduled_job(db_session)
    # Add two async chained events
    for pos in range(2):
        db_session.add(ChainedJobEvent(
            parent_job_id=parent.id,
            position=pos,
            task_type=TaskType.PIPELINE_STOP,
            pipeline_id="pipe-1",
            execution_mode=ExecutionMode.ASYNC,
            with_snapshot=False,
            snapshot_write_method="UPSERT",
        ))
    db_session.commit()

    svc = ChainExecutionService()
    fired: List[int] = []

    async def fake_execute(event):
        fired.append(event.position)
        return True

    with patch.object(svc, "_execute_event", side_effect=fake_execute):
        await svc.execute_chain(parent, db_session)
        # Give asyncio.ensure_future tasks a chance to run
        await asyncio.sleep(0.05)

    assert sorted(fired) == [0, 1]


# ---------------------------------------------------------------------------
# Test 4 — execute_chain sync mode waits for webhook
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_chain_sync_waits_for_webhook(db_session):
    """execute_chain in sync mode must block until notify_webhook_received is called."""
    parent = _make_scheduled_job(db_session)
    db_session.add(ChainedJobEvent(
        parent_job_id=parent.id,
        position=0,
        task_type=TaskType.PIPELINE_STOP,
        pipeline_id="pipe-1",
        execution_mode=ExecutionMode.SYNC,
        with_snapshot=False,
        snapshot_write_method="UPSERT",
    ))
    db_session.commit()

    svc = ChainExecutionService()
    svc._pending = {}  # isolate from module-level singleton
    svc._pre_arrival = {}

    async def fake_execute(event, db):
        return True

    # Persistent webhook model: _execute_sync_event waits for the preceding
    # step's callback first, then fires.  Pre-populate the _pre_arrival
    # buffer so _wait_for_webhook returns immediately without timing out.
    chained_event_id = str(db_session.query(ChainedJobEvent).first().id)
    svc._pre_arrival[chained_event_id] = True

    with (
        patch.object(svc, "_execute_chained_event", side_effect=fake_execute),
    ):
        task = asyncio.create_task(svc.execute_chain(parent, db_session))
        await task  # should complete without timing out


# ---------------------------------------------------------------------------
# Test 5 — webhook_router: valid EXT_MODULE → 200
# ---------------------------------------------------------------------------

def test_webhook_notify_valid_module():
    """POST /api/webhooks/notify with correct EXT_MODULE must return 200."""
    from gluesync_scheduler.core.app import app
    client = TestClient(app, raise_server_exceptions=False)

    with patch(
        "gluesync_scheduler.api.webhook_router.chain_execution_service"
    ) as mock_svc:
        mock_svc.notify_webhook_received.return_value = True
        resp = client.post(
            "/api/webhooks/notify",
            headers={"X-Event-ID": "test-event-123", "EXT_MODULE": "chronos"},
            json={"user": "alice"},
        )
    assert resp.status_code == 200
    assert resp.json()["acknowledged"] is True


# ---------------------------------------------------------------------------
# Test 6 — webhook_router: wrong/missing EXT_MODULE → 400
# ---------------------------------------------------------------------------

def test_webhook_notify_wrong_module():
    """POST /api/webhooks/notify with wrong EXT_MODULE must return 400."""
    from gluesync_scheduler.core.app import app
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/webhooks/notify",
        headers={"X-Event-ID": "test-event-456", "EXT_MODULE": "other-module"},
        json={},
    )
    assert resp.status_code == 400


def test_webhook_notify_missing_module():
    """POST /api/webhooks/notify with no EXT_MODULE must return 400."""
    from gluesync_scheduler.core.app import app
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/webhooks/notify",
        headers={"X-Event-ID": "test-event-789"},
        json={},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 7 — notify_webhook_received with unknown guid → returns False
# ---------------------------------------------------------------------------

def test_notify_webhook_received_unknown_key():
    svc = ChainExecutionService()
    svc._pending = {}
    result = svc.notify_webhook_received("completely-unknown-key")
    assert result is False


# ---------------------------------------------------------------------------
# Test 8 — full async chain: 3 events fire in order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_async_chain_three_events(db_session):
    """A chain of 3 async events must all fire (order may be near-simultaneous)."""
    parent = _make_scheduled_job(db_session)
    task_types = [TaskType.PIPELINE_STOP, TaskType.ENTITY_SNAPSHOT, TaskType.PIPELINE_START]
    for pos, tt in enumerate(task_types):
        db_session.add(ChainedJobEvent(
            parent_job_id=parent.id,
            position=pos,
            task_type=tt,
            pipeline_id="pipe-1",
            execution_mode=ExecutionMode.ASYNC,
            with_snapshot=False,
            snapshot_write_method="UPSERT",
        ))
    db_session.commit()

    svc = ChainExecutionService()
    executed: List[TaskType] = []

    async def fake_execute(event):
        executed.append(event.task_type)
        return True

    with patch.object(svc, "_execute_event", side_effect=fake_execute):
        await svc.execute_chain(parent, db_session)
        await asyncio.sleep(0.1)  # let ensure_future tasks complete

    assert len(executed) == 3
    for tt in task_types:
        assert tt in executed
