#!/usr/bin/env python3
"""
Timezone handling on the standard library zoneinfo (no pytz).

Covers the zone lookup and validation (same names accepted and rejected as before,
same API error), and next_cron_fire on DST nights: a wall time skipped by
spring-forward resolves to the instant pytz's localize(is_dst=False) gave, a wall
time repeated by fall-back matches at both of its occurrences, and the result never
lies an hour off the cron's wall time (which croniter 2.x does with a ZoneInfo start).
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.core.timezone_utils import (
    _wall_time_instants,
    as_utc,
    get_zone,
    is_valid_timezone,
    next_cron_fire,
)

UTC = timezone.utc
ROME = ZoneInfo("Europe/Rome")
NEW_YORK = ZoneInfo("America/New_York")
SYDNEY = ZoneInfo("Australia/Sydney")

# Europe/Rome 2026: spring-forward 29 March 02:00 CET -> 03:00 CEST (01:00Z),
# fall-back 25 October 03:00 CEST -> 02:00 CET (01:00Z)
ROME_SPRING = datetime(2026, 3, 29)
ROME_FALL = datetime(2026, 10, 25)


def _utc(*args):
    return datetime(*args, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Zone lookup and validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["UTC", "Europe/Rome", "America/New_York", "Asia/Tokyo", "Etc/GMT+5"])
def test_get_zone_returns_zoneinfo_whose_str_is_the_name(name):
    tz = get_zone(name)
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == name


@pytest.mark.parametrize("name, key", [("europe/rome", "Europe/Rome"), ("utc", "UTC"), ("AMERICA/NEW_YORK", "America/New_York")])
def test_get_zone_ignores_case_like_pytz(name, key):
    """pytz.timezone accepted any case: names stored that way must still resolve on Linux."""
    assert get_zone(name).key.lower() == key.lower()
    assert get_zone(name).utcoffset(datetime(2026, 7, 1)) == ZoneInfo(key).utcoffset(datetime(2026, 7, 1))


def test_get_zone_unknown_name_raises_not_found():
    with pytest.raises(ZoneInfoNotFoundError):
        get_zone("Mars/Olympus_Mons")


@pytest.mark.parametrize("name", ["", "/etc/localtime", "../Europe/Rome"])
def test_get_zone_malformed_name_raises_value_error(name):
    with pytest.raises((ValueError, ZoneInfoNotFoundError)):
        get_zone(name)


@pytest.mark.parametrize("name", [None, "", "  ", "Mars/Olympus_Mons", "Europe", "/etc/localtime", "../etc/passwd", "Europe/Rome\x00", 42])
def test_is_valid_timezone_rejects_bad_names(name):
    assert is_valid_timezone(name) is False


@pytest.mark.parametrize("name", ["UTC", "Europe/Rome", "europe/rome", "Australia/Lord_Howe"])
def test_is_valid_timezone_accepts_iana_names(name):
    assert is_valid_timezone(name) is True


def test_settings_service_validates_timezone():
    from gluesync_scheduler.services.settings_service import SettingsService

    service = SettingsService.__new__(SettingsService)
    assert service._update_global_timezone("Europe/Rome") is True
    assert service._update_global_timezone("Not/AZone") is False
    assert service._update_global_timezone("") is False


@pytest.mark.parametrize("endpoint", ["update", "create"])
def test_settings_api_rejects_unknown_timezone_with_400(endpoint):
    from gluesync_scheduler.api import settings_router
    from gluesync_scheduler.models.schemas import SettingCreate, SettingUpdate

    if endpoint == "update":
        call = settings_router.update_setting(
            "timezone", SettingUpdate(value="Not/AZone"), db=None, user=None,
        )
    else:
        call = settings_router.create_setting(
            SettingCreate(key="timezone", value="Not/AZone"), db=None, user=None,
        )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(call)
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid timezone: 'Not/AZone'"


def test_as_utc_reads_naive_as_utc_and_keeps_aware():
    assert as_utc(datetime(2026, 1, 1, 12)) == _utc(2026, 1, 1, 12)
    aware = datetime(2026, 1, 1, 12, tzinfo=ROME)
    assert as_utc(aware) is aware


# ---------------------------------------------------------------------------
# localize equivalence on DST nights
# ---------------------------------------------------------------------------

def test_skipped_wall_time_resolves_like_localize_is_dst_false():
    """02:30 does not exist in Rome on 29 March 2026.

    pytz: ROME.localize(datetime(2026, 3, 29, 2, 30)) -> 02:30+01:00 == 01:30Z (03:30 CEST).
    """
    instants = list(_wall_time_instants(ROME_SPRING.replace(hour=2, minute=30), ROME))
    assert instants == [_utc(2026, 3, 29, 1, 30)]


def test_repeated_wall_time_yields_both_occurrences_in_order():
    """02:30 happens twice in Rome on 25 October 2026: 00:30Z (CEST) then 01:30Z (CET).

    pytz localize(is_dst=False) picks the second (01:30Z); croniter with a pytz start
    returned the first. Both are fire candidates, as APScheduler fires both.
    """
    instants = list(_wall_time_instants(ROME_FALL.replace(hour=2, minute=30), ROME))
    assert instants == [_utc(2026, 10, 25, 0, 30), _utc(2026, 10, 25, 1, 30)]


@pytest.mark.parametrize("wall", [datetime(2026, 3, 29, 1, 30), datetime(2026, 3, 29, 3, 30), datetime(2026, 10, 25, 4, 0), datetime(2026, 7, 1, 2, 30)])
def test_unambiguous_wall_time_matches_localize(wall):
    """Away from the transition, localize and replace(tzinfo=...) agree."""
    assert list(_wall_time_instants(wall, ROME)) == [wall.replace(tzinfo=ROME).astimezone(UTC)]


# ---------------------------------------------------------------------------
# next_cron_fire on DST nights
# ---------------------------------------------------------------------------

def test_daily_job_in_skipped_hour_fires_at_the_shifted_instant():
    nxt = next_cron_fire("30 2 * * *", datetime(2026, 3, 28, 12, 0, tzinfo=ROME), ROME)
    assert nxt.astimezone(UTC) == _utc(2026, 3, 29, 1, 30)
    assert nxt.isoformat() == "2026-03-29T03:30:00+02:00"


def test_job_after_spring_forward_keeps_its_wall_time():
    """croniter 2.x with a ZoneInfo (and, from some starts, with pytz) answers 13:00 / 11:00."""
    start = datetime(2026, 3, 29, 0, 7, tzinfo=ROME)
    assert next_cron_fire("0 12 * * *", start, ROME).isoformat() == "2026-03-29T12:00:00+02:00"
    assert next_cron_fire("0 0 * * *", start, ROME).isoformat() == "2026-03-30T00:00:00+02:00"


def test_job_after_fall_back_keeps_its_wall_time():
    start = datetime(2026, 10, 25, 0, 7, tzinfo=ROME)
    assert next_cron_fire("0 12 * * *", start, ROME).isoformat() == "2026-10-25T12:00:00+01:00"
    assert next_cron_fire("0 0 * * *", start, ROME).isoformat() == "2026-10-26T00:00:00+01:00"


def test_daily_job_in_repeated_hour_fires_at_first_occurrence_first():
    nxt = next_cron_fire("30 2 * * *", datetime(2026, 10, 24, 12, 0, tzinfo=ROME), ROME)
    assert nxt.isoformat() == "2026-10-25T02:30:00+02:00"
    assert nxt.astimezone(UTC) == _utc(2026, 10, 25, 0, 30)


def test_next_fire_inside_repeated_hour_is_never_in_the_past():
    """From the second 02:37 (CET), the next */15 is 02:45 CET, not 02:45 CEST an hour ago."""
    now = _utc(2026, 10, 25, 1, 37)  # 02:37 CET, second pass
    nxt = next_cron_fire("*/15 * * * *", now, ROME)
    assert nxt.astimezone(UTC) == _utc(2026, 10, 25, 1, 45)
    assert nxt.isoformat() == "2026-10-25T02:45:00+01:00"


def test_quarter_hour_job_runs_through_the_repeated_hour():
    now = _utc(2026, 10, 24, 23, 50)  # 01:50 CEST
    fires = []
    for _ in range(16):
        now = next_cron_fire("*/15 * * * *", now, ROME)
        fires.append(now)
    utc_fires = [f.astimezone(UTC) for f in fires]
    assert utc_fires == [_utc(2026, 10, 25, 0, 0) + timedelta(minutes=15 * i) for i in range(16)]
    walls = [f.strftime("%H:%M%z") for f in fires[:9]]
    assert walls == ["02:00+0200", "02:15+0200", "02:30+0200", "02:45+0200",
                     "02:00+0100", "02:15+0100", "02:30+0100", "02:45+0100", "03:00+0100"]


def test_hourly_job_skips_nothing_across_spring_forward():
    now = _utc(2026, 3, 28, 23, 30)  # 00:30 CET
    fires = []
    for _ in range(4):
        now = next_cron_fire("0 * * * *", now, ROME)
        fires.append(now.isoformat())
    # 02:00 does not exist: it fires at the shifted 03:00 CEST, once
    assert fires == ["2026-03-29T01:00:00+01:00", "2026-03-29T03:00:00+02:00",
                     "2026-03-29T04:00:00+02:00", "2026-03-29T05:00:00+02:00"]


def test_new_york_and_sydney_transitions():
    # New York spring-forward 8 March 2026 02:00 EST -> 03:00 EDT
    nxt = next_cron_fire("30 2 * * *", datetime(2026, 3, 7, 12, 0, tzinfo=NEW_YORK), NEW_YORK)
    assert nxt.isoformat() == "2026-03-08T03:30:00-04:00"
    # Sydney fall-back 5 April 2026 03:00 AEDT -> 02:00 AEST
    nxt = next_cron_fire("30 2 * * *", datetime(2026, 4, 4, 12, 0, tzinfo=SYDNEY), SYDNEY)
    assert nxt.isoformat() == "2026-04-05T02:30:00+11:00"


def test_day_or_flag_is_honoured():
    start = datetime(2026, 3, 1, 0, 0, tzinfo=ROME)  # Sunday
    # 15th of the month OR Monday -> Monday 2 March; 15th AND Monday -> Monday 15 June
    assert next_cron_fire("0 9 15 * 1", start, ROME, day_or=True).date().isoformat() == "2026-03-02"
    assert next_cron_fire("0 9 15 * 1", start, ROME, day_or=False).date().isoformat() == "2026-06-15"


def test_naive_now_is_read_as_utc():
    assert next_cron_fire("0 * * * *", datetime(2026, 7, 1, 10, 30), ROME).isoformat() == "2026-07-01T13:00:00+02:00"


def test_invalid_cron_raises():
    with pytest.raises(Exception):
        next_cron_fire("not a cron", datetime(2026, 7, 1, tzinfo=UTC), ROME)


_SWEEP_ZONES = ["Europe/Rome", "America/New_York", "Australia/Sydney", "Australia/Lord_Howe", "America/Santiago", "Europe/Dublin", "Asia/Tokyo"]
_SWEEP_CRONS = ["30 2 * * *", "0 2 * * *", "0 1-3 * * *", "*/15 * * * *", "0 0 * * *", "30 1 * * 0", "0 12 * * *", "5 1 * * *"]


def _transitions(tz, year=2026):
    t = datetime(year, 1, 1, tzinfo=UTC)
    prev = t.astimezone(tz).utcoffset()
    found = []
    while t.year == year:
        t += timedelta(minutes=30)
        off = t.astimezone(tz).utcoffset()
        if off != prev:
            found.append(t)
            prev = off
    return found


@pytest.mark.parametrize("zone", _SWEEP_ZONES)
def test_sweep_around_transitions(zone):
    """Around every 2026 transition: the fire is after now, normalized, and on the cron's wall time
    (or, for a wall time the zone skipped, at the instant localize(is_dst=False) gives)."""
    tz = ZoneInfo(zone)
    starts = _transitions(tz) or [datetime(2026, 3, 29, 1, tzinfo=UTC)]
    for transition in starts:
        for step in range(-24 * 2, 24 * 2):
            now = transition + timedelta(minutes=30 * step + 7)
            for cron in _SWEEP_CRONS:
                nxt = next_cron_fire(cron, now, tz)
                assert nxt.astimezone(UTC) > now, (zone, cron, now)
                assert nxt.isoformat() == nxt.astimezone(UTC).astimezone(tz).isoformat()
                assert nxt - now <= timedelta(days=8), (zone, cron, now, nxt)
                minute, hour = cron.split()[:2]
                if minute.isdigit():
                    assert nxt.minute == int(minute) or tz.key == "Australia/Lord_Howe", (zone, cron, nxt)
                if hour.isdigit() and nxt.hour != int(hour):
                    # Only a skipped wall time may land elsewhere, pushed forward by the DST shift
                    wall = nxt.replace(tzinfo=None, hour=int(hour), minute=nxt.minute if not minute.isdigit() else int(minute))
                    assert list(_wall_time_instants(wall, tz))[0] == nxt.astimezone(UTC), (zone, cron, now, nxt)
                    assert wall.replace(tzinfo=tz).astimezone(UTC).astimezone(tz).replace(tzinfo=None) != wall


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def test_job_serializer_reads_naive_as_utc_and_renders_in_job_timezone(monkeypatch):
    from gluesync_scheduler.models.schemas import Job

    job = Job.model_construct(timezone_name="Europe/Rome")
    assert job.serialize_datetime(datetime(2026, 10, 25, 0, 30), None) == "2026-10-25T02:30:00+02:00"
    assert job.serialize_datetime(datetime(2026, 10, 25, 1, 30), None) == "2026-10-25T02:30:00+01:00"
    assert job.serialize_start_time("2026-03-29T01:30:00", None) == "2026-03-29T03:30:00+02:00"


def test_job_serializer_falls_back_to_env_then_utc(monkeypatch):
    from gluesync_scheduler.models.schemas import Job

    monkeypatch.setenv("TZ", "America/New_York")
    job = Job.model_construct(timezone_name="Not/AZone")
    assert job.serialize_datetime(_utc(2026, 7, 1, 12), None) == "2026-07-01T08:00:00-04:00"
    monkeypatch.setenv("TZ", "Not/AZone")
    assert job.serialize_datetime(_utc(2026, 7, 1, 12), None) == "2026-07-01T12:00:00+00:00"


def test_trigger_serializer_reads_naive_as_utc(monkeypatch):
    from gluesync_scheduler.models.trigger_schemas import TriggerFlowResponse

    monkeypatch.setenv("TZ", "Europe/Rome")
    flow = TriggerFlowResponse.model_construct()
    assert flow.serialize_dt(datetime(2026, 3, 29, 1, 30), None) == "2026-03-29T03:30:00+02:00"
