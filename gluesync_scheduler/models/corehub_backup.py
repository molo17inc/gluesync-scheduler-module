#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2026 MOLO17. All rights reserved.

Core Hub backup to Gluesync Connect (task type ``corehub_backup``).

Chronos owns the backup schedule: one job of this task type per Core Hub.
When it fires (or runs through ``POST /api/jobs/{id}/run``) Chronos POSTs
``/backup/scheduled-run`` to Core Hub with the chronos module JWT, the way
``visualize_refresh`` calls ``/visualize/refresh``.

Outcome rules:

- 200 is a successful run, whether Core Hub started the backup or not.
  ``started: false`` carries a ``reason`` (``too_soon``, ``already_running``,
  ``disabled``, ``not_ready``) that becomes the run message.
- 5xx and network errors are retried 3 times (backoff factor 1).
- Any 4xx is a failed run. The job stays enabled: unlike visualize, a 404
  (an older Core Hub without the route) does not disable the schedule.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

CORE_HUB_BACKUP_PATH = "/backup/scheduled-run"
CHRONOS_JOB_ID_HEADER = "X-Chronos-Job-Id"

# Keys of the Chronos internal pipeline route body (not the Hub contract).
JOB_ID = "job_id"
FIRED_AT = "fired_at"
MANUAL = "manual"

REASON_STARTED = "started"

_ERROR_EXCERPT_LEN = 200

# Per attempt. Four attempts plus the 0+2+4 s backoff stay inside the default
# internal HTTP timeout (SCHEDULER_INTERNAL_HTTP_TIMEOUT=120) of the job runner.
CORE_HUB_BACKUP_TIMEOUT_SECONDS = 25

# Same budget as visualize_refresh (total=3, backoff_factor=1): 4xx are terminal,
# only 5xx and network failures are retried.
_HUB_RETRY = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])


def fired_at_now() -> str:
    """Current UTC time as ``2026-10-26T02:30:00Z``."""
    return format_fired_at(datetime.now(timezone.utc))


def format_fired_at(value) -> str:
    """ISO-8601 UTC with a ``Z`` suffix and second precision."""
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _int_or_none(value) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def corehub_backup_internal_payload(job, manual: bool = False, fired_at=None) -> Dict[str, Any]:
    """JSON body for Chronos's own pipeline route (``/pipelines/-/corehub-backup``)."""
    return {
        JOB_ID: _int_or_none(getattr(job, "id", None)),
        FIRED_AT: format_fired_at(fired_at) if fired_at is not None else fired_at_now(),
        MANUAL: bool(manual),
    }


def chain_event_payload(event) -> Dict[str, Any]:
    """Chained events report their parent job; trigger-flow events have none. Never manual."""
    return {
        JOB_ID: _int_or_none(getattr(event, "schedule_id", None)),
        FIRED_AT: fired_at_now(),
        MANUAL: False,
    }


def hub_backup_body(job_id, fired_at, manual: bool) -> Dict[str, Any]:
    """Canonical Hub body: ``{"jobId", "firedAt", "manual"}``."""
    return {
        "jobId": _int_or_none(job_id),
        "firedAt": format_fired_at(fired_at) if fired_at is not None else fired_at_now(),
        "manual": bool(manual),
    }


def hub_backup_headers(job_id) -> Dict[str, str]:
    job_id = _int_or_none(job_id)
    if job_id is None:
        return {}
    return {CHRONOS_JOB_ID_HEADER: str(job_id)}


def _is_error(result) -> bool:
    return isinstance(result, dict) and result.get("status") == "error" and "status_code" in result


def _status_code_of(result) -> Optional[int]:
    if not _is_error(result):
        return None
    try:
        return int(result.get("status_code"))
    except (TypeError, ValueError):
        return None


def _excerpt(text) -> str:
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(collapsed) > _ERROR_EXCERPT_LEN:
        return collapsed[:_ERROR_EXCERPT_LEN] + "..."
    return collapsed


def _backoff_delay(failures: int) -> float:
    """urllib3 Retry.get_backoff_time for this many consecutive failures."""
    if failures <= 1:
        return 0.0
    delay = _HUB_RETRY.backoff_factor * (2 ** (failures - 1))
    return float(max(0.0, min(_HUB_RETRY.backoff_max, delay)))


def _outcome(success: bool, message: str, code, retryable: bool, started=None, reason=None,
             next_allowed_at=None) -> Dict[str, Any]:
    return {
        "success": success,
        "message": message,
        "status_code": code,
        "retryable": retryable,
        "started": started,
        "reason": reason,
        "next_allowed_at": next_allowed_at,
    }


def _started_outcome(result) -> Dict[str, Any]:
    body = result if isinstance(result, dict) else {}
    started = body.get("started")
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = REASON_STARTED if started is not False else "not_started"
    return _outcome(
        True,
        reason.strip(),
        200,
        False,
        started=started if isinstance(started, bool) else None,
        reason=reason.strip(),
        next_allowed_at=body.get("nextAllowedAt"),
    )


def _error_outcome(result) -> Dict[str, Any]:
    code = _status_code_of(result)
    if code is not None and 400 <= code < 500:
        text, retryable = f"Core Hub answered HTTP {code}", False
    elif result.get("error") == "exception":
        text, retryable = "Core Hub unreachable", True
    else:
        text, retryable = f"Core Hub answered HTTP {code or 'error'}", True
    excerpt = _excerpt(result.get("message"))
    if excerpt:
        text = f"{text}: {excerpt}"
    return _outcome(False, text, code, retryable)


def classify_hub_result(result) -> Dict[str, Any]:
    """Map a ``fetch_core_hub(..., raw=True)`` result to success / failure / retryable.

    ``raw=True`` returns the Hub JSON as-is on 2xx and the standard
    ``{"status": "error", "status_code", "message"}`` dict otherwise. ``None``
    means no answer (Core Hub URL unknown, unsupported call) and is retried.
    """
    if result is None:
        return _outcome(False, "Core Hub did not answer", None, True)
    if not _is_error(result):
        return _started_outcome(result)
    return _error_outcome(result)


def post_corehub_backup(
    post: Callable[[str, Dict[str, Any], Dict[str, str]], Any],
    job_id,
    fired_at=None,
    manual: bool = False,
) -> Dict[str, Any]:
    """POST the Hub contract, retrying only 5xx/network with the visualize backoff.

    ``post`` is ``(path, body, headers) -> fetch_core_hub(raw=True) result``.
    401 re-authentication stays inside that call (one refresh, one retry).
    """
    body = hub_backup_body(job_id, fired_at, manual)
    headers = hub_backup_headers(job_id)
    failures = 0
    while True:
        outcome = classify_hub_result(post(CORE_HUB_BACKUP_PATH, body, headers))
        if not outcome["retryable"]:
            return outcome
        failures += 1
        if failures > _HUB_RETRY.total:
            outcome["message"] = f"{outcome['message']} (after {failures} attempts)"
            return outcome
        delay = _backoff_delay(failures)
        logger.warning(
            "Core Hub backup job_id=%s attempt %s failed (%s); retrying in %ss",
            job_id, failures, outcome["message"], delay,
        )
        if delay:
            time.sleep(delay)
