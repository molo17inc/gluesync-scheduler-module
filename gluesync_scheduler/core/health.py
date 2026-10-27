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
 * Copyright (C) 2026 MOLO17. All rights reserved.
"""

"""
Liveness endpoint for the Chronos container.

``GET /health`` is registered on the root app (not under ``/api``), so the reverse
proxy routes that expose ``/api/`` never publish it. It is unauthenticated and only
reads in-memory state: no network and no database access happen while serving it.

Because the handler runs on the asyncio event loop, a loop that is stuck (for
instance in a scheduler busy loop) cannot answer it. The Docker HEALTHCHECK then
times out and marks the container unhealthy, which is the purpose of the probe.

This module is unrelated to ``health_check.py``, which refreshes the Core Hub token.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
from gluesync_scheduler.services.scheduler_service import scheduler_service
from gluesync_scheduler.version import __version__

logger = logging.getLogger(__name__)

router = APIRouter()

# Monotonic reference taken when the module is imported, i.e. at process start-up
_STARTED_AT = time.monotonic()

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"


def _scheduler_state() -> Tuple[bool, Optional[int]]:
    """Return (running, scheduled job count) from the in-memory APScheduler."""
    try:
        scheduler = getattr(scheduler_service, "scheduler", None)
        running = bool(scheduler is not None and scheduler.running)
    except Exception:
        logger.exception("Could not read the scheduler state")
        return False, None

    try:
        jobs = len(scheduler.get_jobs()) if running else 0
    except Exception:
        logger.exception("Could not count the scheduled jobs")
        jobs = None
    return running, jobs


def _corehub_connected() -> bool:
    """Current SDK connection state, without touching the network."""
    try:
        client = gluesync_sdk_client.client
        return bool(client is not None and client.is_connected)
    except Exception:
        logger.exception("Could not read the Core Hub connection state")
        return False


def build_health() -> Tuple[int, Dict[str, Any]]:
    """Build the HTTP status code and the JSON body of the health report."""
    running, jobs = _scheduler_state()
    connected = _corehub_connected()

    if not running:
        status, http_status = STATUS_DOWN, 503
    elif not connected:
        # Core Hub being unreachable must not make Chronos unhealthy
        status, http_status = STATUS_DEGRADED, 200
    else:
        status, http_status = STATUS_OK, 200

    return http_status, {
        "status": status,
        "version": __version__,
        "uptime_seconds": int(time.monotonic() - _STARTED_AT),
        "scheduler": {"running": running, "jobs": jobs},
        "corehub": {"connected": connected},
    }


@router.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Liveness probe: 200 while the scheduler runs, 503 when it does not."""
    http_status, body = build_health()
    return JSONResponse(status_code=http_status, content=body)
