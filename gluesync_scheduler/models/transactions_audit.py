#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2026 MOLO17. All rights reserved.

Transactions audit start and stop (task types ``pipeline_audit_start`` and
``pipeline_audit_stop``).

When the job fires, or runs through ``POST /api/jobs/{id}/run``, Chronos POSTs
to Core Hub with the chronos module JWT:

- start: ``POST /pipelines/{id}/transactions-audit/start`` with
  ``{"entityIds": [...], "confirmStatements": true}``
- stop:  ``POST /pipelines/{id}/transactions-audit/stop`` with
  ``{"entityIds": [...]}``

An empty ``entityIds`` means the configured scope (start) or every entity
(stop). Both calls are idempotent in Core Hub, so 5xx and network errors are
retried. Any 4xx fails the run with Core Hub's own message and code; the job
stays enabled.

``confirmStatements`` is always true: whoever creates the schedule confirms the
statements Core Hub shows in its preflight (decision recorded in the merge
request).

Extension point, retention purge: see ``AUDIT_PURGE_TASK_TYPES_PLACEHOLDER``.
"""

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

AUDIT_START = "start"
AUDIT_STOP = "stop"

CORE_HUB_AUDIT_PATH = "/pipelines/{pipeline_id}/transactions-audit/{action}"

# Per attempt. Three attempts plus the 0+2 s backoff stay inside the default
# internal HTTP timeout (SCHEDULER_INTERNAL_HTTP_TIMEOUT=120) of the job runner.
CORE_HUB_AUDIT_TIMEOUT_SECONDS = 30
# Retries after the first attempt (5xx and network errors only).
CORE_HUB_AUDIT_RETRIES = 2
_BACKOFF_FACTOR = 1
_ERROR_EXCERPT_LEN = 200

# ---------------------------------------------------------------------------
# EXTENSION POINT: retention purge.
# Retention purge does not exist in Core Hub yet and NO purge task is
# implemented here. When it does, add its task type (for example
# ``pipeline_audit_purge``) to this tuple and wire it where the two types
# below are wired: models.TaskType, schemas description, job_service and
# chain_execution_service action maps, job_runner endpoint, pipeline_router
# route, CoreHubClient/PipelineManager method, and the Core Hub MCP taskTypes
# list (corehub-mcp ChronosTools.kt).
# ---------------------------------------------------------------------------
AUDIT_PURGE_TASK_TYPES_PLACEHOLDER: tuple = ()


def _int_list(value) -> List[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return [v for v in value if v not in (None, "")]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [v for v in parsed if v not in (None, "")] if isinstance(parsed, list) else []


def audit_hub_body(action: str, entity_ids=None) -> Dict[str, Any]:
    """Hub request body: start confirms the statements, stop carries only the entities."""
    body: Dict[str, Any] = {"entityIds": [str(e) for e in _int_list(entity_ids)]}
    if action == AUDIT_START:
        body["confirmStatements"] = True
    return body


def audit_hub_path(action: str, pipeline_id: str) -> str:
    return CORE_HUB_AUDIT_PATH.format(pipeline_id=pipeline_id, action=action)


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
    if failures <= 1:
        return 0.0
    return float(_BACKOFF_FACTOR * (2 ** (failures - 1)))


def _outcome(success: bool, message: str, code, retryable: bool, state=None) -> Dict[str, Any]:
    return {
        "success": success,
        "message": message,
        "status_code": code,
        "retryable": retryable,
        "state": state,
    }


def _success_message(action: str, body) -> (str, Optional[str]):
    verb = "started" if action == AUDIT_START else "stopped"
    if not isinstance(body, dict):
        return f"Audit {verb}", None
    state = body.get("state") if isinstance(body.get("state"), str) else None
    recording, total = body.get("recordingCount"), body.get("totalCount")
    parts = [f"Audit {verb}"]
    if state:
        parts.append(f"state {state}")
    if isinstance(recording, int) and isinstance(total, int):
        parts.append(f"{recording} of {total} entities recording")
    return ", ".join(parts), state


def _hub_error_text(code, result) -> str:
    """Core Hub's message and code when the body is its ErrorResponseDto, else a body excerpt."""
    raw = result.get("message")
    detail = ""
    try:
        body = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        detail = _excerpt(body["message"])
        extras = [str(body[k]) for k in ("code",) if body.get(k)]
        if extras:
            detail = f"{detail} ({extras[0]})"
        if body.get("field"):
            detail = f"{detail} [field {body['field']}]"
    else:
        detail = _excerpt(raw)
    text = f"Core Hub answered HTTP {code}"
    return f"{text}: {detail}" if detail else text


def _error_outcome(result) -> Dict[str, Any]:
    code = _status_code_of(result)
    if code is not None and 400 <= code < 500:
        return _outcome(False, _hub_error_text(code, result), code, False)
    if result.get("error") == "exception":
        text, retryable = "Core Hub unreachable", True
        excerpt = _excerpt(result.get("message"))
        return _outcome(False, f"{text}: {excerpt}" if excerpt else text, code, retryable)
    return _outcome(False, _hub_error_text(code or "error", result), code, True)


def classify_hub_result(action: str, result) -> Dict[str, Any]:
    """Map a ``fetch_core_hub(..., raw=True)`` result to success / failure / retryable."""
    if result is None:
        return _outcome(False, "Core Hub did not answer", None, True)
    if not _is_error(result):
        message, state = _success_message(action, result)
        return _outcome(True, message, 200, False, state=state)
    return _error_outcome(result)


def post_audit_action(
    post: Callable[[str, Dict[str, Any]], Any],
    action: str,
    pipeline_id: str,
    entity_ids=None,
) -> Dict[str, Any]:
    """POST start or stop, retrying only 5xx and network errors.

    ``post`` is ``(path, body) -> fetch_core_hub(raw=True)`` result. A 401
    re-authentication stays inside that call (one refresh, one retry).
    """
    path = audit_hub_path(action, pipeline_id)
    body = audit_hub_body(action, entity_ids)
    failures = 0
    while True:
        outcome = classify_hub_result(action, post(path, body))
        if not outcome["retryable"]:
            return outcome
        failures += 1
        if failures > CORE_HUB_AUDIT_RETRIES:
            outcome["message"] = f"{outcome['message']} (after {failures} attempts)"
            return outcome
        delay = _backoff_delay(failures)
        logger.warning(
            "Transactions audit %s on pipeline %s attempt %s failed (%s); retrying in %ss",
            action, pipeline_id, failures, outcome["message"], delay,
        )
        if delay:
            time.sleep(delay)
