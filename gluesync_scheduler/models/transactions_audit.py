#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2026 MOLO17. All rights reserved.

Transactions audit tasks: ``pipeline_audit_start``, ``pipeline_audit_stop``,
``pipeline_audit_group_start``, ``pipeline_audit_group_stop`` and
``pipeline_audit_purge``.

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

Group start and stop POST ``/pipelines/{id}/transactions-audit/groups/{groupId}/start``
(``{"confirmStatements": true}``) and ``.../stop`` (empty body), once per group id.

Purge POSTs ``/pipelines/{id}/transactions-audit/purge`` with
``{"olderThanDays": n, "entityIds": [...]}`` (both optional; without
``olderThanDays`` Core Hub applies the ``retentionDays`` of the configuration).
Core Hub stops a call at its row or time budget and answers ``complete: false``;
Chronos then calls again within the same run, at most ``PURGE_MAX_ROUNDS``
rounds and ``PURGE_DEADLINE_SECONDS`` seconds, logging each round. A run that
hits the cap succeeds (rows were deleted, nothing is wrong) and says the purge
is incomplete; the next run goes on.
"""

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from gluesync_scheduler.models.corehub_backup import (
    _backoff_delay,
    _excerpt,
    _is_error,
    _status_code_of,
)

logger = logging.getLogger(__name__)

AUDIT_START = "start"
AUDIT_STOP = "stop"

CORE_HUB_AUDIT_PATH = "/pipelines/{pipeline_id}/transactions-audit/{action}"

# Per attempt. Three attempts plus the 0+2 s backoff stay inside the default
# internal HTTP timeout (SCHEDULER_INTERNAL_HTTP_TIMEOUT=120) of the job runner.
CORE_HUB_AUDIT_TIMEOUT_SECONDS = 30
# Retries after the first attempt (5xx and network errors only).
CORE_HUB_AUDIT_RETRIES = 2

AUDIT_GROUP_START = "group-start"
AUDIT_GROUP_STOP = "group-stop"

CORE_HUB_AUDIT_GROUP_PATH = "/pipelines/{pipeline_id}/transactions-audit/groups/{group_id}/{action}"
CORE_HUB_AUDIT_PURGE_PATH = "/pipelines/{pipeline_id}/transactions-audit/purge"

# Purge. Core Hub ends one call at its own budget (5 minutes, or 1 000 000 rows) and
# answers ``complete: false``; the per-call timeout outlives that budget.
CORE_HUB_PURGE_TIMEOUT_SECONDS = 330
CORE_HUB_PURGE_RETRIES = 1
# At most this many calls per run (about 10 000 000 rows at Core Hub's default row budget)...
PURGE_MAX_ROUNDS = 10
# ...and no new round once the run is this old.
PURGE_DEADLINE_SECONDS = 2700
_PURGE_HTTP_MARGIN_SECONDS = 60
MAX_OLDER_THAN_DAYS = 36500


def purge_http_timeout_seconds() -> int:
    """Internal HTTP timeout of the purge route: it blocks until the last round ends."""
    return (
        PURGE_DEADLINE_SECONDS
        + (CORE_HUB_PURGE_RETRIES + 1) * CORE_HUB_PURGE_TIMEOUT_SECONDS
        + _PURGE_HTTP_MARGIN_SECONDS
    )


def check_older_than_days(older_than_days) -> None:
    """Reject an age that is not a whole number of days from 1 to 36500. None is fine (the retention applies)."""
    if older_than_days is None:
        return
    if isinstance(older_than_days, bool) or not isinstance(older_than_days, int) \
            or not 1 <= older_than_days <= MAX_OLDER_THAN_DAYS:
        raise ValueError(f"audit_older_than_days must be a whole number from 1 to {MAX_OLDER_THAN_DAYS}")


def require_audit_purge_fields(task_type, older_than_days) -> None:
    """Validate the purge age. No-op for other task types."""
    from gluesync_scheduler.models.models import TaskType

    if task_type == TaskType.PIPELINE_AUDIT_PURGE:
        check_older_than_days(older_than_days)


def audit_purge_internal_payload(older_than_days, entity_ids) -> Dict[str, Any]:
    """Body of Chronos's own purge route, shared by jobs, chained and trigger events."""
    payload: Dict[str, Any] = {}
    if older_than_days is not None:
        payload["audit_older_than_days"] = older_than_days
    if entity_ids:
        payload["entity_ids"] = entity_ids
    return payload


def audit_purge_orm_kwargs(obj) -> Dict[str, Any]:
    return {"audit_older_than_days": getattr(obj, "audit_older_than_days", None)}


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


def _outcome(success: bool, message: str, code, retryable: bool, state=None) -> Dict[str, Any]:
    return {
        "success": success,
        "message": message,
        "status_code": code,
        "retryable": retryable,
        "state": state,
    }


def _success_message(action: str, body) -> Tuple[str, Optional[str]]:
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
        if body.get("code"):
            detail = f"{detail} ({body['code']})"
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


def _classify(result, describe: Callable[[Any], Tuple[str, Optional[str]]]) -> Dict[str, Any]:
    if result is None:
        return _outcome(False, "Core Hub did not answer", None, True)
    if not _is_error(result):
        message, state = describe(result)
        outcome = _outcome(True, message, 200, False, state=state)
        outcome["body"] = result
        return outcome
    return _error_outcome(result)


def classify_hub_result(action: str, result) -> Dict[str, Any]:
    """Map a ``fetch_core_hub(..., raw=True)`` result to success / failure / retryable."""
    return _classify(result, lambda body: _success_message(action, body))


def _post_with_retries(
    post: Callable[[str, Dict[str, Any]], Any],
    path: str,
    body: Dict[str, Any],
    classify: Callable[[Any], Dict[str, Any]],
    retries: int,
    label: str,
) -> Dict[str, Any]:
    """POST and classify, retrying only 5xx and network errors (``retries`` after the first attempt)."""
    failures = 0
    while True:
        outcome = classify(post(path, body))
        if not outcome["retryable"]:
            return outcome
        failures += 1
        if failures > retries:
            outcome["message"] = f"{outcome['message']} (after {failures} attempts)"
            return outcome
        delay = _backoff_delay(failures)
        logger.warning("%s attempt %s failed (%s); retrying in %ss", label, failures, outcome["message"], delay)
        if delay:
            time.sleep(delay)


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
    return _post_with_retries(
        post,
        audit_hub_path(action, pipeline_id),
        audit_hub_body(action, entity_ids),
        lambda result: classify_hub_result(action, result),
        CORE_HUB_AUDIT_RETRIES,
        f"Transactions audit {action} on pipeline {pipeline_id}",
    )


# --- group start and stop ----------------------------------------------------

def audit_group_hub_body(action: str) -> Dict[str, Any]:
    """Group start confirms the statements; group stop has no body."""
    return {"confirmStatements": True} if action == AUDIT_GROUP_START else {}


def audit_group_hub_path(action: str, pipeline_id: str, group_id: str) -> str:
    verb = "start" if action == AUDIT_GROUP_START else "stop"
    return CORE_HUB_AUDIT_GROUP_PATH.format(
        pipeline_id=pipeline_id, group_id=quote(str(group_id), safe=""), action=verb,
    )


def _entity_outcome_counts(body: Dict[str, Any]) -> Dict[str, int]:
    """Count the per-entity ``outcome`` strings of a group audit answer."""
    counts: Dict[str, int] = {}
    for entity in body.get("entities") or []:
        outcome = entity.get("outcome") if isinstance(entity, dict) else None
        if isinstance(outcome, str):
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def _group_state(body: Dict[str, Any]) -> Optional[str]:
    status = body.get("status")
    if isinstance(status, dict) and isinstance(status.get("state"), str):
        return status["state"]
    return None


def _group_success_message(action: str, group_id: str, body) -> Tuple[str, Optional[str]]:
    verb = "started" if action == AUDIT_GROUP_START else "stopped"
    name = body.get("groupName") if isinstance(body, dict) and isinstance(body.get("groupName"), str) else None
    parts = [f"Audit {verb} for group {name or group_id}"]
    state = None
    if isinstance(body, dict):
        counts = _entity_outcome_counts(body)
        if counts:
            parts.append(", ".join(f"{n} {o.lower().replace('_', ' ')}" for o, n in sorted(counts.items())))
        state = _group_state(body)
        if state is not None:
            parts.append(f"state {state}")
    return ", ".join(parts), state


def classify_group_result(action: str, group_id: str, result) -> Dict[str, Any]:
    return _classify(result, lambda body: _group_success_message(action, group_id, body))


def post_audit_group_action(
    post: Callable[[str, Dict[str, Any]], Any],
    action: str,
    pipeline_id: str,
    group_ids,
) -> Dict[str, Any]:
    """Start or stop the audit of each group, one Core Hub call per group.

    Every group is attempted; the run fails when any of them failed, with every message.
    """
    groups = [str(g) for g in _int_list(group_ids)]
    if not groups:
        return _outcome(False, "A group audit task needs at least one group id", 400, False)
    body = audit_group_hub_body(action)
    messages: List[str] = []
    failures: List[Dict[str, Any]] = []
    state = None
    for group_id in groups:
        outcome = _post_with_retries(
            post,
            audit_group_hub_path(action, pipeline_id, group_id),
            body,
            lambda result, g=group_id: classify_group_result(action, g, result),
            CORE_HUB_AUDIT_RETRIES,
            f"Transactions audit {action} of group {group_id} on pipeline {pipeline_id}",
        )
        if outcome["success"]:
            messages.append(outcome["message"])
            state = outcome.get("state") or state
        else:
            failures.append(outcome)
            messages.append(f"Group {group_id}: {outcome['message']}")
    if not failures:
        return _outcome(True, "; ".join(messages), 200, False, state=state)
    # A 4xx fails the run for good; if every failure was 5xx or network, the caller may report 502.
    retryable = all(f["retryable"] for f in failures)
    return _outcome(False, "; ".join(messages), failures[0]["status_code"], retryable)


# --- purge ---------------------------------------------------------------------

def audit_purge_hub_body(older_than_days=None, entity_ids=None) -> Dict[str, Any]:
    body: Dict[str, Any] = {}
    if older_than_days not in (None, ""):
        body["olderThanDays"] = int(older_than_days)
    body["entityIds"] = [str(e) for e in _int_list(entity_ids)]
    return body


def audit_purge_hub_path(pipeline_id: str) -> str:
    return CORE_HUB_AUDIT_PURGE_PATH.format(pipeline_id=pipeline_id)


def _purge_round_message(body) -> Tuple[str, Optional[str]]:
    if not isinstance(body, dict):
        return "Purge round answered", None
    return (
        f"{body.get('rowsDeleted', 0)} rows deleted in {body.get('batches', 0)} batches "
        f"(cutoff {body.get('cutoff')}, retention {body.get('retentionDays')} days)",
        None,
    )


def post_audit_purge(
    post: Callable[[str, Dict[str, Any]], Any],
    pipeline_id: str,
    older_than_days=None,
    entity_ids=None,
    max_rounds: int = PURGE_MAX_ROUNDS,
    deadline_seconds: float = PURGE_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Any]:
    """Purge until Core Hub answers ``complete: true`` or the round or time cap is hit.

    Each round is one Core Hub call (5xx and network errors retried once). A failed
    round fails the run, keeping the rows already counted in the message. Hitting
    the cap, or a round that deleted nothing without finishing, ends the run as a
    success that says the purge is incomplete.
    """
    path = audit_purge_hub_path(pipeline_id)
    body = audit_purge_hub_body(older_than_days, entity_ids)
    started = clock()
    total_rows = 0
    rounds = 0
    while True:
        rounds += 1
        outcome = _post_with_retries(
            post, path, body, lambda result: _classify(result, _purge_round_message),
            CORE_HUB_PURGE_RETRIES, f"Transactions audit purge on pipeline {pipeline_id} round {rounds}",
        )
        if not outcome["success"]:
            return _failed_purge_round(outcome, rounds, total_rows)
        answer = outcome.get("body") if isinstance(outcome.get("body"), dict) else {}
        rows = answer.get("rowsDeleted") if isinstance(answer.get("rowsDeleted"), int) else 0
        total_rows += rows
        complete = answer.get("complete") is not False
        logger.info(
            "Transactions audit purge pipeline=%s round=%s/%s: %s rows deleted, complete=%s",
            pipeline_id, rounds, max_rounds, rows, complete,
        )
        cap_reached = rounds >= max_rounds or clock() - started >= deadline_seconds
        finished = _purge_round_end(rounds, rows, total_rows, complete, cap_reached)
        if finished is not None:
            return finished


def _failed_purge_round(outcome: Dict[str, Any], rounds: int, total_rows: int) -> Dict[str, Any]:
    """Keep the rows already deleted in the message of a failed purge round."""
    if total_rows:
        outcome["message"] = f"{outcome['message']} (round {rounds}; {total_rows} rows deleted before it)"
    return outcome


def _purge_round_end(
    rounds: int, rows: int, total_rows: int, complete: bool, cap_reached: bool,
) -> Optional[Dict[str, Any]]:
    """Outcome that ends the purge after a successful round, or None to go on."""
    if complete:
        return _purge_outcome(f"Purge complete after {rounds} round(s), {total_rows} rows deleted", total_rows, rounds, True)
    if rows == 0:
        return _purge_outcome(
            f"Purge incomplete: round {rounds} made no progress, {total_rows} rows deleted; the next run goes on",
            total_rows, rounds, False,
        )
    if cap_reached:
        return _purge_outcome(
            f"Purge incomplete after {rounds} round(s) (cap reached), {total_rows} rows deleted; the next run goes on",
            total_rows, rounds, False,
        )
    return None


def _purge_outcome(message: str, rows: int, rounds: int, complete: bool) -> Dict[str, Any]:
    outcome = _outcome(True, message, 200, False)
    outcome.update(rows_deleted=rows, rounds=rounds, complete=complete)
    return outcome
