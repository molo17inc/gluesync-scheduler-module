"""GS-1495: an event's run rows must carry their offset, like the event's Last started."""
from datetime import datetime, timezone

import pytest

from gluesync_scheduler.models.trigger_schemas import ExecutionLogResponse


def _log(triggered_at):
    return ExecutionLogResponse(
        id=1, trigger_flow_id=1, status="success", source="webhook", duration_ms=10,
        triggered_at=triggered_at,
    )


@pytest.mark.parametrize("tz_name, expected", [
    ("Asia/Tokyo", "2026-11-28T09:32:21+09:00"),
    ("UTC", "2026-11-28T00:32:21+00:00"),
])
def test_a_naive_sqlite_value_is_utc_and_shown_in_the_scheduler_timezone(monkeypatch, tz_name, expected):
    monkeypatch.setenv("TZ", tz_name)
    body = _log(datetime(2026, 11, 28, 0, 32, 21)).model_dump(mode="json")
    assert body["triggered_at"] == expected


def test_an_aware_value_keeps_its_instant(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    body = _log(datetime(2026, 11, 28, 0, 32, 21, tzinfo=timezone.utc)).model_dump(mode="json")
    assert body["triggered_at"] == "2026-11-28T09:32:21+09:00"
