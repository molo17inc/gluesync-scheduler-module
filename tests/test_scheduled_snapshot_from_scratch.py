# -*- coding: utf-8 -*-
"""A scheduled snapshot must always reset the migration checkpoint.

GSSD-1346: the scheduled snapshot POSTed one-time-snapshot without `fromScratch`,
which defaults to false on CoreHub. When the previous run had been interrupted its
migration checkpoint survived, and the next run resumed positionally into a query
that had meanwhile selected a different set of rows: CoreHub skipped the first N
rows of the new day and reported the snapshot as completed.

A scheduled run is independent of the previous one, so it must always start from
scratch. Note this resets the migration checkpoint only, unlike redo, which also
drops the CDC read position.
"""

from unittest.mock import patch

import pytest

from gluesync_scheduler.core.play_pause import CoreHubClient


@pytest.fixture
def client():
    CoreHubClient._instance = None
    CoreHubClient._discovered_url = "http://localhost:1717"
    instance = CoreHubClient()
    yield instance
    CoreHubClient._instance = None


def _params_of(fetch):
    fetch.assert_called_once()
    return fetch.call_args.kwargs["params"]


def test_resync_entity_asks_for_a_snapshot_from_scratch(client):
    with patch.object(client, "stop_entity", return_value=True), patch(
        "time.sleep"
    ), patch.object(client, "fetch_core_hub", return_value={}) as fetch:
        client.resync_entity("p1", "e1")

    params = _params_of(fetch)
    assert params["fromScratch"] == "true"
    assert params["entity"] == "e1"


def test_resync_pipeline_asks_for_a_snapshot_from_scratch(client):
    with patch.object(client, "stop_pipeline", return_value=True), patch(
        "time.sleep"
    ), patch.object(client, "fetch_core_hub", return_value={}) as fetch:
        client.resync_pipeline("p1")

    assert _params_of(fetch)["fromScratch"] == "true"


def test_resync_group_asks_for_a_snapshot_from_scratch(client):
    with patch.object(client, "stop_group", return_value=True), patch(
        "time.sleep"
    ), patch.object(client, "fetch_core_hub", return_value={}) as fetch:
        client.resync_group("p1", "g1")

    params = _params_of(fetch)
    assert params["fromScratch"] == "true"
    assert params["groupId"] == "g1"


def test_snapshot_write_method_is_left_untouched(client):
    """fromScratch is orthogonal to INSERT/UPSERT: it must not change the write method."""
    with patch.object(client, "stop_entity", return_value=True), patch(
        "time.sleep"
    ), patch.object(client, "fetch_core_hub", return_value={}) as fetch:
        client.resync_entity("p1", "e1", snapshot_write_method="INSERT")

    assert _params_of(fetch)["snapshotWriteMethod"] == "INSERT"


def test_scheduled_snapshot_still_targets_the_one_time_snapshot_endpoint(client):
    """It must not become a redo: redo would also drop the CDC read position."""
    with patch.object(client, "stop_entity", return_value=True), patch(
        "time.sleep"
    ), patch.object(client, "fetch_core_hub", return_value={}) as fetch:
        client.resync_entity("p1", "e1")

    path = fetch.call_args.args[0] if fetch.call_args.args else fetch.call_args.kwargs["path"]
    assert path.endswith("/commands/sync/one-time-snapshot")
