# -*- coding: utf-8 -*-
"""Unit tests for pause-then-poll-Hold before redo (Chronos !19)."""

import os
from unittest.mock import patch

import pytest

from gluesync_scheduler.core.play_pause import CoreHubClient


HOLD = {"entityId": "e1", "isSyncActive": False, "isMigrationActive": False, "errorState": None}
ACTIVE = {"entityId": "e1", "isSyncActive": True, "isMigrationActive": False, "errorState": None}


@pytest.fixture
def client():
    CoreHubClient._instance = None
    CoreHubClient._discovered_url = "http://localhost:1717"
    os.environ["CHRONOS_REDO_PAUSE_TIMEOUT"] = "2"
    os.environ["CHRONOS_REDO_POLL_INTERVAL"] = "0"
    instance = CoreHubClient()
    yield instance
    CoreHubClient._instance = None


def test_is_entity_hold_true_when_idle(client):
    assert client._is_entity_hold(HOLD) is True


def test_is_entity_hold_false_when_sync_active(client):
    assert client._is_entity_hold(ACTIVE) is False


def test_is_entity_hold_false_when_migration_active(client):
    entry = {"entityId": "e1", "isSyncActive": False, "isMigrationActive": True, "errorState": None}
    assert client._is_entity_hold(entry) is False


def test_is_entity_hold_false_when_error_state_set(client):
    entry = {"entityId": "e1", "isSyncActive": False, "isMigrationActive": False, "errorState": "FAILED"}
    assert client._is_entity_hold(entry) is False


def test_is_entity_hold_false_for_non_dict(client):
    assert client._is_entity_hold(None) is False
    assert client._is_entity_hold("hold") is False


@pytest.mark.parametrize(
    "entry,expected",
    [
        ({"entityId": "a"}, "a"),
        ({"entityID": "b"}, "b"),
        ({"id": "c"}, "c"),
        ({"entity_id": "d"}, "d"),
        ({}, None),
    ],
)
def test_entity_status_id(client, entry, expected):
    assert client._entity_status_id(entry) == expected


def test_get_pipeline_entities_status_list(client):
    with patch.object(client, "fetch_core_hub", return_value=[HOLD]):
        assert client.get_pipeline_entities_status("p1") == [HOLD]


def test_get_pipeline_entities_status_wrapped_keys(client):
    with patch.object(client, "fetch_core_hub", return_value={"entities": [HOLD]}):
        assert client.get_pipeline_entities_status("p1") == [HOLD]
    with patch.object(client, "fetch_core_hub", return_value={"statuses": [HOLD]}):
        assert client.get_pipeline_entities_status("p1") == [HOLD]


def test_get_pipeline_entities_status_empty_on_failure(client):
    with patch.object(client, "fetch_core_hub", return_value=None):
        assert client.get_pipeline_entities_status("p1") == []


def test_wait_for_entities_paused_empty_ids(client):
    assert client._wait_for_entities_paused("p1", []) is True


def test_wait_for_entities_paused_already_hold(client):
    with patch.object(client, "get_pipeline_entities_status", return_value=[HOLD]):
        assert client._wait_for_entities_paused("p1", ["e1"], timeout=1, poll_interval=0) is True


def test_wait_for_entities_paused_polls_then_hold(client):
    with patch.object(
        client,
        "get_pipeline_entities_status",
        side_effect=[[ACTIVE], [HOLD]],
    ), patch("gluesync_scheduler.core.play_pause.time.sleep"):
        assert client._wait_for_entities_paused("p1", ["e1"], timeout=5, poll_interval=0) is True


def test_wait_for_entities_paused_times_out(client):
    with patch.object(client, "get_pipeline_entities_status", return_value=[ACTIVE]), patch(
        "gluesync_scheduler.core.play_pause.time.sleep"
    ), patch("gluesync_scheduler.core.play_pause.time.time", side_effect=[0, 0.1, 10]):
        assert client._wait_for_entities_paused("p1", ["e1"], timeout=1, poll_interval=0) is False


def test_wait_for_entities_paused_missing_entity_is_pending(client):
    with patch.object(client, "get_pipeline_entities_status", return_value=[]), patch(
        "gluesync_scheduler.core.play_pause.time.sleep"
    ), patch("gluesync_scheduler.core.play_pause.time.time", side_effect=[0, 0.1, 10]):
        assert client._wait_for_entities_paused("p1", ["e1"], timeout=1, poll_interval=0) is False


def test_redo_entity_aborts_when_pause_fails(client):
    with patch.object(client, "stop_entity", return_value=False) as stop, patch.object(
        client, "fetch_core_hub"
    ) as fetch:
        assert client.redo_entity("p1", "e1", with_snapshot=True) is False
        stop.assert_called_once_with("p1", "e1")
        fetch.assert_not_called()


def test_redo_entity_aborts_when_hold_times_out(client):
    with patch.object(client, "stop_entity", return_value=True), patch.object(
        client, "_wait_for_entities_paused", return_value=False
    ), patch.object(client, "fetch_core_hub") as fetch:
        assert client.redo_entity("p1", "e1") is False
        fetch.assert_not_called()


def test_redo_entity_posts_redo_after_hold(client):
    with patch.object(client, "stop_entity", return_value=True), patch.object(
        client, "_wait_for_entities_paused", return_value=True
    ), patch.object(
        client, "fetch_core_hub", return_value={"status": "success"}
    ) as fetch:
        assert client.redo_entity("p1", "e1", with_snapshot=True, snapshot_write_method="INSERT") is True
        fetch.assert_called_once()
        args, kwargs = fetch.call_args
        assert args[0] == "/pipelines/p1/commands/sync/redo"
        assert kwargs["method"] == "POST"
        assert kwargs["params"]["entity"] == "e1"
        assert kwargs["params"]["withSnapshot"] == "true"
        assert kwargs["params"]["snapshotWriteMethod"] == "INSERT"


def test_redo_pipeline_aborts_when_pause_fails(client):
    with patch.object(client, "stop_pipeline", return_value=False), patch.object(
        client, "fetch_core_hub"
    ) as fetch:
        assert client.redo_pipeline("p1") is False
        fetch.assert_not_called()


def test_redo_pipeline_aborts_when_hold_times_out(client):
    with patch.object(client, "stop_pipeline", return_value=True), patch.object(
        client, "get_pipeline_entities", return_value=[{"id": "e1"}]
    ), patch.object(client, "_wait_for_entities_paused", return_value=False), patch.object(
        client, "fetch_core_hub"
    ) as fetch:
        assert client.redo_pipeline("p1") is False
        fetch.assert_not_called()


def test_redo_pipeline_proceeds_when_entity_ids_unresolved(client):
    with patch.object(client, "stop_pipeline", return_value=True), patch.object(
        client, "get_pipeline_entities", return_value=[]
    ), patch.object(
        client, "fetch_core_hub", return_value={"status": "success"}
    ) as fetch:
        assert client.redo_pipeline("p1", with_snapshot=True) is True
        assert fetch.call_args[0][0] == "/pipelines/p1/commands/sync/redo"
        assert fetch.call_args[1]["params"]["withSnapshot"] == "true"


def test_redo_group_aborts_when_pause_fails(client):
    with patch.object(client, "stop_group", return_value=False), patch.object(
        client, "fetch_core_hub"
    ) as fetch:
        assert client.redo_group("p1", "g1") is False
        fetch.assert_not_called()


def test_redo_group_posts_redo_group_after_hold(client):
    with patch.object(client, "stop_group", return_value=True), patch.object(
        client, "_get_group_entity_ids", return_value=["e1"]
    ), patch.object(client, "_wait_for_entities_paused", return_value=True), patch.object(
        client, "fetch_core_hub", return_value={"status": "success"}
    ) as fetch:
        assert client.redo_group("p1", "g1", with_snapshot=True) is True
        assert fetch.call_args[0][0] == "/pipelines/p1/commands/sync/redo-group"
        assert fetch.call_args[1]["params"]["groupId"] == "g1"
        assert fetch.call_args[1]["params"]["withSnapshot"] == "true"


def test_get_group_entity_ids_filters_by_group(client):
    entities = [
        {"id": "e1", "groupId": "g1"},
        {"entityId": "e2", "groupId": "g2"},
        {"id": "e3", "groupId": "g1"},
    ]
    with patch.object(client, "fetch_core_hub", return_value=entities):
        assert client._get_group_entity_ids("p1", "g1") == ["e1", "e3"]

def test_get_pipeline_entities_status_data_and_items(client):
    with patch.object(client, "fetch_core_hub", return_value={"data": [HOLD]}):
        assert client.get_pipeline_entities_status("p1") == [HOLD]
    with patch.object(client, "fetch_core_hub", return_value={"items": [HOLD]}):
        assert client.get_pipeline_entities_status("p1") == [HOLD]


def test_get_pipeline_entities_status_bare_dict(client):
    payload = {"entityId": "e1", "isSyncActive": False}
    with patch.object(client, "fetch_core_hub", return_value=payload):
        assert client.get_pipeline_entities_status("p1") == [payload]


def test_redo_pipeline_posts_redo_after_hold(client):
    with patch.object(client, "stop_pipeline", return_value=True), patch.object(
        client, "get_pipeline_entities", return_value=[{"id": "e1"}]
    ), patch.object(client, "_wait_for_entities_paused", return_value=True), patch.object(
        client, "fetch_core_hub", return_value={"status": "success"}
    ) as fetch:
        assert client.redo_pipeline("p1", with_snapshot=True) is True
        assert fetch.call_args[0][0] == "/pipelines/p1/commands/sync/redo"
        assert fetch.call_args[1]["params"]["withSnapshot"] == "true"


def test_redo_group_aborts_when_hold_times_out(client):
    with patch.object(client, "stop_group", return_value=True), patch.object(
        client, "_get_group_entity_ids", return_value=["e1"]
    ), patch.object(client, "_wait_for_entities_paused", return_value=False), patch.object(
        client, "fetch_core_hub"
    ) as fetch:
        assert client.redo_group("p1", "g1") is False
        fetch.assert_not_called()


def test_redo_group_proceeds_when_entity_ids_unresolved(client):
    with patch.object(client, "stop_group", return_value=True), patch.object(
        client, "_get_group_entity_ids", return_value=[]
    ), patch.object(
        client, "fetch_core_hub", return_value={"status": "success"}
    ) as fetch:
        assert client.redo_group("p1", "g1", with_snapshot=True) is True
        assert fetch.call_args[0][0] == "/pipelines/p1/commands/sync/redo-group"


def test_get_group_entity_ids_empty_on_failure(client):
    with patch.object(client, "fetch_core_hub", return_value=None):
        assert client._get_group_entity_ids("p1", "g1") == []
