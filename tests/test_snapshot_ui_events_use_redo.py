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

"""UI snapshot events share the CoreHub redo path (withSnapshot=true)."""

import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from gluesync_scheduler.models.models import TaskType
from gluesync_scheduler.services.chain_execution_service import (
    ExecutableEvent,
    ExecutionMode,
    _task_type_to_action,
    ChainExecutionService,
)


def _ok_response():
    response = MagicMock()
    response.status_code = 200
    response.text = '{"success": true}'
    response.headers = {}
    return response


def _job(task_type, *, write_method="UPSERT", with_snapshot=False, entity_ids=None, group_ids=None):
    job = MagicMock()
    job.id = 1
    job.name = "snapshot-ui"
    job.task_type = task_type
    job.pipeline_id = "pipe-1"
    job.entity_ids = entity_ids
    job.group_ids = group_ids
    job.with_snapshot = with_snapshot
    job.snapshot_write_method = write_method
    job.cron_job_identifier = "job-1"
    return job


def _assert_no_resync(payload):
    keys = {str(k).lower() for k in payload}
    assert "resync" not in keys
    assert all("resync" not in str(v).lower() for v in payload.values() if not isinstance(v, (list, dict)))


def _job_service():
    mock_scheduler_module = MagicMock()
    mock_scheduler_module.scheduler_service = MagicMock()
    sys.modules['gluesync_scheduler.services.scheduler_service'] = mock_scheduler_module
    from gluesync_scheduler.services.job_service import JobService
    return JobService(db=MagicMock())


@pytest.mark.parametrize(
    "task_type,write_method,entity_ids,expected_suffix",
    [
        (TaskType.ENTITY_SNAPSHOT, "INSERT", '["ent-1"]', "/pipelines/pipe-1/redo"),
        (TaskType.ENTITY_SNAPSHOT, "UPSERT", '["ent-1"]', "/pipelines/pipe-1/redo"),
        (TaskType.PIPELINE_SNAPSHOT, "INSERT", None, "/pipelines/pipe-1/redo"),
        (TaskType.PIPELINE_SNAPSHOT, "UPSERT", None, "/pipelines/pipe-1/redo"),
        (TaskType.ENTITY_REDO, "INSERT", '["ent-1"]', "/pipelines/pipe-1/redo"),
        (TaskType.PIPELINE_REDO, "UPSERT", None, "/pipelines/pipe-1/redo"),
    ],
)
def test_job_service_snapshot_and_redo_hit_redo(task_type, write_method, entity_ids, expected_suffix):
    service = _job_service()
    job = _job(task_type, write_method=write_method, entity_ids=entity_ids, with_snapshot=False)

    with patch("gluesync_scheduler.services.job_service.requests.request", return_value=_ok_response()) as req:
        success, message, _details = service._execute_job_logic(job)

    assert success is True
    _url = req.call_args.kwargs["url"]
    assert expected_suffix in _url
    assert "one-time-snapshot" not in _url
    payload = req.call_args.kwargs["json"]
    snapshot_types = (TaskType.ENTITY_SNAPSHOT, TaskType.PIPELINE_SNAPSHOT)
    if task_type in snapshot_types:
        # Snapshot UI events always force withSnapshot=true on the redo path.
        assert payload["with_snapshot"] is True
    else:
        # ENTITY_REDO / PIPELINE_REDO honor the job flag (False in this fixture).
        assert "with_snapshot" not in payload
    assert payload["snapshot_write_method"] == write_method
    _assert_no_resync(payload)
    if entity_ids:
        assert payload["entity_ids"] == ["ent-1"]


def test_job_service_group_snapshot_uses_redo_group_with_snapshot():
    service = _job_service()
    job = _job(TaskType.GROUP_SNAPSHOT, write_method="INSERT", group_ids='["g-1"]', with_snapshot=False)

    with patch("gluesync_scheduler.core.play_pause.CoreHubClient") as client_cls:
        client = MagicMock()
        client.redo_group.return_value = True
        client_cls.return_value = client
        success, message, details = service._execute_job_logic(job)

    assert success is True
    client.redo_group.assert_called_once_with(
        "pipe-1",
        "g-1",
        with_snapshot=True,
        snapshot_write_method="INSERT",
    )
    assert client.resync_group.call_count == 0


@pytest.mark.parametrize(
    "task_type,expected",
    [
        (TaskType.ENTITY_SNAPSHOT, "redo"),
        (TaskType.PIPELINE_SNAPSHOT, "redo"),
        (TaskType.GROUP_SNAPSHOT, "redo-group"),
        (TaskType.ENTITY_REDO, "redo"),
        (TaskType.PIPELINE_REDO, "redo"),
        (TaskType.GROUP_REDO, "redo-group"),
    ],
)
def test_chain_task_type_maps_snapshot_to_redo(task_type, expected):
    assert _task_type_to_action(task_type) == expected


@pytest.mark.parametrize(
    "task_type,write_method,entity_ids,group_ids,expected_suffix",
    [
        (TaskType.ENTITY_SNAPSHOT, "INSERT", '["ent-1"]', None, "/pipelines/pipe-1/redo"),
        (TaskType.PIPELINE_SNAPSHOT, "UPSERT", None, None, "/pipelines/pipe-1/redo"),
        (TaskType.GROUP_SNAPSHOT, "INSERT", None, '["g-1"]', "/pipelines/pipe-1/redo-group"),
        (TaskType.ENTITY_REDO, "UPSERT", '["ent-1"]', None, "/pipelines/pipe-1/redo"),
        (TaskType.GROUP_REDO, "INSERT", None, '["g-1"]', "/pipelines/pipe-1/redo-group"),
    ],
)
def test_chain_execute_event_posts_redo(task_type, write_method, entity_ids, group_ids, expected_suffix):
    event = ExecutableEvent(
        id=1,
        position=0,
        task_type=task_type,
        pipeline_id="pipe-1",
        entity_ids=entity_ids,
        group_ids=group_ids,
        with_snapshot=False,
        snapshot_write_method=write_method,
        execution_mode=ExecutionMode.SYNC,
    )
    service = ChainExecutionService()

    with patch("gluesync_scheduler.services.chain_execution_service.requests.post", return_value=_ok_response()) as req:
        ok = asyncio.run(service._execute_event(event))

    assert ok is True
    url = req.call_args.kwargs.get("url") or req.call_args.args[0]
    assert expected_suffix in url
    assert "one-time-snapshot" not in url
    payload = req.call_args.kwargs["json"]
    if task_type in (TaskType.ENTITY_SNAPSHOT, TaskType.PIPELINE_SNAPSHOT, TaskType.GROUP_SNAPSHOT):
        assert payload["with_snapshot"] is True
    assert payload["snapshot_write_method"] == write_method
    _assert_no_resync(payload)


@pytest.mark.parametrize(
    "task_type,write_method,entity_ids,group_ids,expected_suffix",
    [
        (TaskType.ENTITY_SNAPSHOT, "INSERT", '["ent-1"]', None, "/pipelines/pipe-1/redo"),
        (TaskType.PIPELINE_SNAPSHOT, "UPSERT", None, None, "/pipelines/pipe-1/redo"),
        (TaskType.GROUP_SNAPSHOT, "INSERT", None, '["g-1"]', "/pipelines/pipe-1/redo-group"),
        (TaskType.ENTITY_REDO, "INSERT", '["ent-1"]', None, "/pipelines/pipe-1/redo"),
        (TaskType.PIPELINE_REDO, "UPSERT", None, None, "/pipelines/pipe-1/redo"),
        (TaskType.GROUP_REDO, "INSERT", None, '["g-1"]', "/pipelines/pipe-1/redo-group"),
    ],
)
def test_job_runner_snapshot_and_redo_hit_redo(task_type, write_method, entity_ids, group_ids, expected_suffix):
    os.makedirs("/tmp/chronos-test-data", exist_ok=True)
    os.environ.setdefault("DATA_DIR", "/tmp/chronos-test-data")
    os.environ.setdefault("DB_URL", "sqlite:////tmp/chronos-test-data/scheduler.db")
    os.environ.setdefault("HOST", "localhost")
    os.environ.setdefault("PORT", "8000")
    os.environ.setdefault("SSL_ENABLED", "False")

    from gluesync_scheduler.cli.job_runner import execute_job

    job = _job(
        task_type,
        write_method=write_method,
        with_snapshot=False,
        entity_ids=entity_ids,
        group_ids=group_ids,
    )

    with patch("gluesync_scheduler.cli.job_runner.requests.post", return_value=_ok_response()) as req:
        assert execute_job(job) is True

    url = req.call_args.args[0]
    payload = req.call_args.kwargs["json"]
    assert expected_suffix in url
    assert "one-time-snapshot" not in url
    if task_type in (TaskType.ENTITY_SNAPSHOT, TaskType.PIPELINE_SNAPSHOT, TaskType.GROUP_SNAPSHOT):
        assert payload["with_snapshot"] is True
    assert payload["snapshot_write_method"] == write_method
    _assert_no_resync(payload)


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
