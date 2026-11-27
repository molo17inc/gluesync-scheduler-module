#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

import os

BRAIN_JOB_PATH = "/api/ai/v1/brain/job"

STATUS_CREATING = "CREATING"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"


def brain_reindex_timeout_seconds() -> int:
    """How long Chronos waits for a brain rebuild to leave CREATING."""
    raw = os.getenv("SCHEDULER_BRAIN_REINDEX_TIMEOUT_SECONDS", "3600")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 3600


def brain_reindex_poll_seconds() -> float:
    raw = os.getenv("SCHEDULER_BRAIN_REINDEX_POLL_SECONDS", "2")
    try:
        return max(0.2, float(raw))
    except (TypeError, ValueError):
        return 2.0


def brain_reindex_http_timeout_seconds() -> int:
    """Internal HTTP budget: the route blocks until the rebuild is terminal."""
    return brain_reindex_timeout_seconds() + 30


def brain_job_status(job: dict) -> str:
    return str((job or {}).get("status") or "").upper()


def _job_count(job: dict, camel_key: str, snake_key: str):
    """Counter from the brain job, camelCase first, snake_case fallback, else 0."""
    value = job.get(camel_key)
    if value is None:
        value = job.get(snake_key) or 0
    return value


def brain_job_message(job: dict) -> str:
    """Human-readable outcome stored as the job error when the rebuild fails."""
    job = job or {}
    status = brain_job_status(job)
    message = str(job.get("message") or "").strip()
    tables = _job_count(job, "tablesSeen", "tables_seen")
    edges = _job_count(job, "edgesSeen", "edges_seen")
    if status == STATUS_READY:
        base = message or "Enterprise brain is ready."
        return f"{base} ({tables} tables, {edges} edges)"
    if status == STATUS_FAILED:
        return message or "Enterprise brain reindex failed."
    return message or f"Enterprise brain reindex ended with status {status or 'unknown'}."


def brain_rebuild_settled(job: dict, saw_creating: bool, before_generation, before_known: bool) -> bool:
    """True once this trigger's rebuild has reached READY or FAILED.

    A READY/FAILED row that was already stored before the trigger is ignored,
    otherwise a fast poll would report the previous build. A new generation,
    or having observed CREATING, means this trigger's rebuild moved.
    """
    status = brain_job_status(job)
    if status not in (STATUS_READY, STATUS_FAILED):
        return False
    if saw_creating:
        return True
    generation = (job or {}).get("generation")
    return bool(before_known and generation and generation != before_generation)
