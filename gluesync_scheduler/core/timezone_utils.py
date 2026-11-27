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

import functools
import logging
import os
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from croniter import croniter

logger = logging.getLogger(__name__)


def get_env_timezone(default: str = "UTC") -> str:
    """Return the effective timezone from environment variables.

    Priority order:
    1. TZ
    2. TIMEZONE (deprecated)
    3. Provided default (UTC by default)

    When falling back to TIMEZONE, a deprecation warning is logged.
    """
    tz = os.getenv("TZ")
    if tz:
        return tz

    legacy_tz = os.getenv("TIMEZONE")
    if legacy_tz:
        logger.warning(
            "Environment variable TIMEZONE is deprecated and will be removed in a "
            "future release. Please use TZ instead."
        )
        return legacy_tz

    return default


# What ZoneInfo raises for a name it cannot load: unknown key, malformed key
# (absolute path, '..', empty), or a key that names a directory of the tz database
_ZONE_LOAD_ERRORS = (ZoneInfoNotFoundError, ValueError, TypeError, OSError)


@functools.lru_cache(maxsize=1)
def _zone_keys_by_lower() -> dict:
    return {key.lower(): key for key in available_timezones()}


def get_zone(name: str) -> ZoneInfo:
    """The ZoneInfo for an IANA timezone name.

    The lookup ignores case, as the timezone library Chronos used before zoneinfo did ('europe/rome' -> Europe/Rome), so names
    that were accepted and stored before the move to zoneinfo still resolve, whatever the
    case sensitivity of the filesystem the tz database lives on.

    Raises ZoneInfoNotFoundError for an unknown name and ValueError for a malformed one.
    """
    if not isinstance(name, str):
        raise ValueError(f"Timezone name must be a string, not {type(name).__name__}")
    try:
        return ZoneInfo(name)
    except _ZONE_LOAD_ERRORS:
        key = _zone_keys_by_lower().get(name.lower())
        if key is None:
            raise
        return ZoneInfo(key)


def is_valid_timezone(name: Optional[str]) -> bool:
    """True when the name is an IANA timezone name get_zone can load."""
    try:
        get_zone(name)
        return True
    except _ZONE_LOAD_ERRORS:
        return False


def as_utc(dt: datetime) -> datetime:
    """Aware datetime for a value that may be naive: naive datetimes are UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# Longest jump of a UTC offset at a DST change (2h historically, 1h or less today)
_MAX_DST_SHIFT = timedelta(hours=3)
_MAX_CRON_CANDIDATES = 10000


def next_cron_fire(cron_expression: str, now: datetime, tz: tzinfo, day_or: bool = True) -> datetime:
    """The first time strictly after `now` whose wall-clock time in `tz` matches the cron.

    croniter 2.x is not fold-aware: given a ZoneInfo-aware start it returns times an hour
    off around DST changes. So the cron runs on naive wall-clock times and each one is
    turned into an instant here:
    - a wall time skipped by spring-forward (02:30 in Europe/Rome on the last Sunday of
      March) is read with the offset in force before the change (fold=0), so it fires at
      03:30 local, as localize(is_dst=False) of the earlier timezone library and
      APScheduler do;
    - a wall time repeated by fall-back matches at both of its occurrences, so a
      sub-hourly cron keeps firing through the repeated hour, as APScheduler fires it.
    The result is in `tz`, normalized (correct offset and fold).
    """
    now_utc = as_utc(now).astimezone(timezone.utc)
    wall_now = now_utc.astimezone(tz).replace(tzinfo=None)
    # Start early enough that a wall time repeated after `now` is not stepped over
    cron_iter = croniter(cron_expression, wall_now - _MAX_DST_SHIFT, day_or=day_or)
    best: Optional[datetime] = None
    best_wall: Optional[datetime] = None
    for _ in range(_MAX_CRON_CANDIDATES):
        wall = cron_iter.get_next(datetime)
        if best_wall is not None and wall > best_wall + _MAX_DST_SHIFT:
            break
        for instant in _wall_time_instants(wall, tz):
            if instant > now_utc and (best is None or instant < best):
                best, best_wall = instant, wall
    if best is None:
        raise ValueError(f"No fire time found for cron '{cron_expression}' after {now.isoformat()}")
    return best.astimezone(tz)


def _wall_time_instants(wall: datetime, tz: tzinfo):
    """The UTC instants a naive wall time stands for in tz: one, two in a repeated hour."""
    first = wall.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    yield first
    second = wall.replace(tzinfo=tz, fold=1).astimezone(timezone.utc)
    # fold=1 is a distinct real time only in a repeated hour; in a skipped hour it maps
    # back to a different wall time and is dropped
    if second != first and second.astimezone(tz).replace(tzinfo=None) == wall:
        yield second
