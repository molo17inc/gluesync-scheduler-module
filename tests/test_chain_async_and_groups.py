#!/usr/bin/env python3
"""Chained events: a trailing async event runs (trailing-async-chained-event-dropped) and
group_start / group_stop act on the groups only (group-chained-events-act-on-whole-pipeline)."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.models.models import ExecutionMode, TaskType
from gluesync_scheduler.services.chain_execution_service import (
    ChainExecutionService,
    _event_payload,
    _task_type_to_action,
)


def _event(pos, mode, task_type=TaskType.PIPELINE_STOP):
    return SimpleNamespace(position=pos, task_type=task_type, execution_mode=mode, pipeline_id="p", id=pos)


def test_a_trailing_async_event_finishes_inside_asyncio_run():
    svc = ChainExecutionService()
    done = []

    async def slow_async(event, db=None):
        await asyncio.sleep(0.05)
        done.append(event.position)
        return True

    async def sync_ok(event, db, preceding):
        done.append(event.position)
        return True

    job = SimpleNamespace(task_type=TaskType.PIPELINE_STOP)
    events = [_event(0, ExecutionMode.SYNC), _event(1, ExecutionMode.ASYNC)]
    with patch.object(svc, "_execute_chained_event", side_effect=slow_async), \
            patch.object(svc, "_execute_sync_event", side_effect=sync_ok):
        asyncio.run(svc._run_chained_events(job, events, None, []))
    assert done == [0, 1]


def test_async_events_still_do_not_block_the_next_step():
    svc = ChainExecutionService()
    order = []

    async def slow_async(event, db=None):
        await asyncio.sleep(0.05)
        order.append(f"async-{event.position}")
        return True

    async def sync_ok(event, db, preceding):
        order.append(f"sync-{event.position}")
        return True

    job = SimpleNamespace(task_type=TaskType.PIPELINE_STOP)
    events = [_event(0, ExecutionMode.ASYNC), _event(1, ExecutionMode.SYNC)]
    with patch.object(svc, "_execute_chained_event", side_effect=slow_async), \
            patch.object(svc, "_execute_sync_event", side_effect=sync_ok):
        asyncio.run(svc._run_chained_events(job, events, None, []))
    assert order == ["sync-1", "async-0"]


def test_trigger_flow_trailing_async_event_finishes():
    svc = ChainExecutionService()
    done = []

    async def run(event):
        await asyncio.sleep(0.05)
        done.append(event.position)
        return True

    with patch.object(svc, "_execute_event", side_effect=run):
        assert asyncio.run(svc._run_executable_list([_event(0, ExecutionMode.SYNC), _event(1, ExecutionMode.ASYNC)])) is True
    assert sorted(done) == [0, 1]


def test_a_failing_async_event_does_not_break_the_chain():
    svc = ChainExecutionService()

    async def boom(event, db=None):
        raise RuntimeError("down")

    job = SimpleNamespace(task_type=TaskType.PIPELINE_STOP)
    with patch.object(svc, "_execute_chained_event", side_effect=boom):
        asyncio.run(svc._run_chained_events(job, [_event(0, ExecutionMode.ASYNC)], None, []))


@pytest.mark.parametrize("task_type,action", [(TaskType.GROUP_START, "start-group"), (TaskType.GROUP_STOP, "stop-group")])
def test_group_start_stop_use_the_group_routes(task_type, action):
    assert _task_type_to_action(task_type) == action
    ev = SimpleNamespace(task_type=task_type, entity_ids=None, group_ids='["g1"]', with_snapshot=False,
                         snapshot_write_method=None, execution_mode=ExecutionMode.ASYNC)
    assert _event_payload(ev)["group_ids"] == ["g1"]


def test_group_routes_exist():
    from gluesync_scheduler.core.app import app
    paths = {route.path for route in app.routes}
    assert "/api/pipelines/{pipeline_id}/start-group" in paths
    assert "/api/pipelines/{pipeline_id}/stop-group" in paths


def test_start_groups_calls_core_hub_once_per_group():
    from gluesync_scheduler.core.play_pause import PipelineManager
    calls = []
    manager = PipelineManager.__new__(PipelineManager)
    manager.client = SimpleNamespace(
        start_group=lambda p, g, snap, method: calls.append(("start", p, g, snap, method)) or True,
        stop_group=lambda p, g: calls.append(("stop", p, g)) or True,
    )
    assert asyncio.run(manager.start_groups("p", ["a", "b"], True, "INSERT")) is True
    assert asyncio.run(manager.stop_groups("p", ["a"])) is True
    assert calls == [("start", "p", "a", True, "INSERT"), ("start", "p", "b", True, "INSERT"), ("stop", "p", "a")]
