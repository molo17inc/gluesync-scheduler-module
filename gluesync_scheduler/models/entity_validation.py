#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.

Helpers for the ``entity_validate`` task type: Chronos asks the CoreHub
Validator (data comparison) to compare an entity between source and target,
optionally reconciling the target when differences are found.
"""

import os
from typing import Any, Dict, Iterable, List, Optional

VALIDATION_RECONCILE = "validation_reconcile"

# Statuses reported by CoreHub `GET /data-comparison/runs/{jobId}`.
VALIDATION_TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED"}
VALIDATION_FAILURE_STATUSES = {"FAILED", "CANCELLED"}

_DEFAULT_TIMEOUT_SECONDS = 3600
_DEFAULT_POLL_SECONDS = 2
_HTTP_TIMEOUT_MARGIN_SECONDS = 30


def validation_timeout_seconds() -> int:
    """How long Chronos waits for a single validation run to reach a terminal status."""
    raw = os.getenv("SCHEDULER_VALIDATION_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


def validation_poll_seconds() -> float:
    raw = os.getenv("SCHEDULER_VALIDATION_POLL_SECONDS", str(_DEFAULT_POLL_SECONDS))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(_DEFAULT_POLL_SECONDS)
    return value if value > 0 else float(_DEFAULT_POLL_SECONDS)


def validation_http_timeout_seconds(entity_count: int = 1) -> int:
    """Internal HTTP timeout for a validate call: the router blocks until every
    entity has been validated, so the caller must outlive the poll deadline."""
    count = max(1, int(entity_count or 1))
    return validation_timeout_seconds() * count + _HTTP_TIMEOUT_MARGIN_SECONDS


def require_entity_validation_fields(task_type, entity_ids) -> None:
    """Validate ENTITY_VALIDATE fields. No-op for other task types."""
    from gluesync_scheduler.models.models import TaskType

    if task_type != TaskType.ENTITY_VALIDATE:
        return
    ids = _coerce_id_list(entity_ids)
    if not ids:
        raise ValueError("entity_validate requires at least one entity_id")


def entity_validation_orm_kwargs(obj) -> Dict[str, Any]:
    return {
        VALIDATION_RECONCILE: bool(getattr(obj, VALIDATION_RECONCILE, False)),
    }


def entity_validation_http_payload(obj, entity_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Build the internal Chronos ``/validate`` JSON body from a job/event object."""
    ids = _coerce_id_list(entity_ids if entity_ids is not None else getattr(obj, "entity_ids", None))
    return {
        "entity_ids": ids,
        VALIDATION_RECONCILE: bool(getattr(obj, VALIDATION_RECONCILE, False)),
    }


def total_differences(run: Optional[Dict[str, Any]]) -> int:
    summary = run or {}
    return sum(
        _as_int(summary.get(key))
        for key in (
            "missingInSourceCount",
            "missingInTargetCount",
            "rowMismatchesCount",
            "schemaDifferencesCount",
        )
    )


def validation_run_summary(run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Compact, JSON-safe view of a CoreHub run summary for job details / logs."""
    summary = run or {}
    return {
        "jobId": summary.get("jobId"),
        "status": summary.get("status"),
        "sourceRowsScanned": _as_int(summary.get("sourceRowsScanned")),
        "targetRowsScanned": _as_int(summary.get("targetRowsScanned")),
        "missingInSourceCount": _as_int(summary.get("missingInSourceCount")),
        "missingInTargetCount": _as_int(summary.get("missingInTargetCount")),
        "rowMismatchesCount": _as_int(summary.get("rowMismatchesCount")),
        "schemaDifferencesCount": _as_int(summary.get("schemaDifferencesCount")),
        "totalDifferences": total_differences(summary),
        "errorCode": summary.get("errorCode"),
        "errorMessage": summary.get("errorMessage"),
    }


def describe_differences(run: Optional[Dict[str, Any]]) -> str:
    """Human readable breakdown, e.g. ``3 only in source, 0 only in target, ...``."""
    summary = run or {}
    return (
        f"{_as_int(summary.get('missingInTargetCount'))} only in source, "
        f"{_as_int(summary.get('missingInSourceCount'))} only in target, "
        f"{_as_int(summary.get('rowMismatchesCount'))} mismatching rows, "
        f"{_as_int(summary.get('schemaDifferencesCount'))} schema differences"
    )


def _coerce_id_list(value: Any) -> List[str]:
    from gluesync_scheduler.models.ai_agent_run import parse_json_list

    parsed = parse_json_list(value) if not isinstance(value, (list, tuple, set)) else list(value)
    return [str(item).strip() for item in parsed if str(item).strip()]


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
