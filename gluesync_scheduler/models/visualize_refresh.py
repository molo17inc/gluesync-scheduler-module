#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

import json
import logging
import time
from typing import Any, Callable, Dict, Optional

from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

VISUALIZE_REFRESH_TASK = "visualize.refresh"
VISUALIZE_REFRESH_PATH = "/visualize/refresh"
VIZ_ID = "viz_id"
VISUALIZE_PARAMETERS = "visualize_parameters"

# Same budget as GroupService HTTP retries (total=3, backoff_factor=1).
# 4xx, including 429, are not retried: the visualize contract treats them as
# terminal. Only 5xx and network failures use this backoff.
_HUB_RETRY = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])


def require_visualize_refresh_fields(task_type, viz_id) -> None:
    """Validate VISUALIZE_REFRESH fields. No-op for other task types."""
    from gluesync_scheduler.models.models import TaskType

    if task_type != TaskType.VISUALIZE_REFRESH:
        return
    if not viz_id or not str(viz_id).strip():
        raise ValueError("visualize_refresh requires a non-empty viz_id")


def coerce_parameter_map(value) -> Optional[Dict[str, str]]:
    """Return a string map, or None when empty. Reject nested values."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("visualize parameters must be a string map")
    cleaned: Dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip()
        if not name:
            continue
        if isinstance(item, (dict, list)):
            raise ValueError("visualize parameters must be a string map")
        cleaned[name] = "" if item is None else str(item)
    return cleaned or None


def parameters_of(obj) -> Optional[Dict[str, str]]:
    raw = getattr(obj, VISUALIZE_PARAMETERS, None)
    if raw in (None, "", {}):
        raw = getattr(obj, "parameters", None)
    return coerce_parameter_map(raw)


def schedule_id_of(obj) -> Optional[int]:
    """Chronos schedule id, or None when this run has no schedule."""
    explicit = getattr(obj, "schedule_id", None)
    if explicit not in (None, ""):
        return int(explicit)
    if getattr(obj, "cron_job_identifier", None) and getattr(obj, "id", None) is not None:
        return int(obj.id)
    parent = getattr(obj, "parent_job_id", None)
    if parent is not None:
        return int(parent)
    return None


def visualize_refresh_orm_kwargs(obj) -> Dict[str, Any]:
    params = parameters_of(obj)
    return {
        VIZ_ID: getattr(obj, VIZ_ID, None),
        VISUALIZE_PARAMETERS: json.dumps(params) if params else None,
    }


def visualize_internal_payload(obj, schedule_id=None) -> Dict[str, Any]:
    """JSON body for Chronos's own pipeline route (not the Hub contract)."""
    payload: Dict[str, Any] = {VIZ_ID: getattr(obj, VIZ_ID, None)}
    params = parameters_of(obj)
    if params:
        payload["parameters"] = params
    sid = schedule_id if schedule_id is not None else schedule_id_of(obj)
    if sid is not None:
        payload["schedule_id"] = int(sid)
    return payload


def hub_refresh_body(viz_id: str, parameters=None, schedule_id=None) -> Dict[str, Any]:
    """Canonical Hub body. Omits parameters when empty and scheduleId when absent."""
    body: Dict[str, Any] = {
        "task": VISUALIZE_REFRESH_TASK,
        "vizId": str(viz_id).strip(),
    }
    params = coerce_parameter_map(parameters)
    if params:
        body["parameters"] = params
    if schedule_id is not None and schedule_id != "":
        body["scheduleId"] = int(schedule_id)
    return body


def refresh_accepted(status_code: Optional[int], body: Optional[dict] = None) -> bool:
    """202 is acceptance. ``scheduled`` true and false are both success."""
    if status_code != 202:
        return False
    if not isinstance(body, dict) or "scheduled" not in body:
        return True
    return isinstance(body.get("scheduled"), bool)


def _status_code_of(result) -> Optional[int]:
    if not isinstance(result, dict):
        return None
    if result.get("status") not in ("success", "error"):
        return None
    code = result.get("status_code")
    if code is None:
        return 500 if result.get("status") == "error" else None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _retryable_status(code: Optional[int]) -> bool:
    return code is None or code >= 500


def _backoff_delay(failures: int) -> float:
    """urllib3 Retry.get_backoff_time for this many consecutive failures."""
    if failures <= 1:
        return 0.0
    delay = _HUB_RETRY.backoff_factor * (2 ** (failures - 1))
    return float(max(0.0, min(_HUB_RETRY.backoff_max, delay)))


def _outcome(success: bool, message: str, code, retryable: bool, disable: bool) -> Dict[str, Any]:
    return {
        "success": success,
        "message": message,
        "status_code": code,
        "retryable": retryable,
        "disable_schedule": disable,
    }


def classify_hub_result(result) -> Dict[str, Any]:
    """Map a fetch_core_hub result to success / terminal failure / retryable."""
    code = _status_code_of(result)
    if isinstance(result, dict) and result.get("status") == "success" and refresh_accepted(code):
        return _outcome(True, "Visualize refresh accepted", code, False, False)
    if code == 404:
        return _outcome(
            False,
            "Visualization not found; disabling schedule",
            code,
            False,
            True,
        )
    if code is not None and 400 <= code < 500:
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or "")
        text = message.strip() or f"Visualize refresh rejected with HTTP {code}"
        return _outcome(False, text[:500], code, False, False)
    if _retryable_status(code):
        return _outcome(False, f"Visualize refresh failed with HTTP {code or 'network'}", code, True, False)
    return _outcome(False, "Visualize refresh failed", code, False, False)


def disable_visualize_schedule(schedule_id) -> bool:
    """Disable the Chronos schedule after Hub reports the visualization is gone."""
    from gluesync_scheduler.db.database import get_db
    from gluesync_scheduler.models.models import ScheduledJob
    from gluesync_scheduler.services.scheduler_service import scheduler_service

    db = next(get_db())
    try:
        job = db.query(ScheduledJob).filter(ScheduledJob.id == int(schedule_id)).first()
        if job is None:
            logger.info("No schedule %s to disable", schedule_id)
            return False
        job.enabled = False
        if not job.command:
            job.command = "disabled"
        db.commit()
        try:
            scheduler_service.remove_job(job.id)
        except Exception:
            logger.exception("Failed to remove schedule %s from the scheduler", schedule_id)
        logger.info("Disabled visualize refresh schedule %s", schedule_id)
        return True
    finally:
        db.close()


def post_visualize_refresh(
    post: Callable[[str, Dict[str, Any]], Any],
    viz_id: str,
    parameters=None,
    schedule_id=None,
) -> Dict[str, Any]:
    """POST the Hub contract once, retrying only 5xx/network with the existing backoff.

    ``post`` is ``(path, body) -> fetch_core_hub result``. 401 re-authentication
    stays inside that call (one refresh, one retry) and is not repeated here.
    """
    if not viz_id or not str(viz_id).strip():
        return _outcome(False, "visualize_refresh requires a non-empty viz_id", 400, False, False)

    body = hub_refresh_body(viz_id, parameters, schedule_id)
    failures = 0
    result = None
    while True:
        result = post(VISUALIZE_REFRESH_PATH, body)
        outcome = classify_hub_result(result)
        if not outcome["retryable"]:
            break
        failures += 1
        if failures > _HUB_RETRY.total:
            break
        delay = _backoff_delay(failures)
        if delay:
            time.sleep(delay)

    if outcome["disable_schedule"] and schedule_id is not None and schedule_id != "":
        disable_visualize_schedule(schedule_id)
    return outcome
