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

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum, Text, Boolean, ForeignKey
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.sqltypes import TIMESTAMP

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.db.database import Base


class TaskType(enum.Enum):
    ENTITY_START = "entity_start"
    ENTITY_STOP = "entity_stop"
    PIPELINE_START = "pipeline_start"
    PIPELINE_STOP = "pipeline_stop"
    ENTITY_SNAPSHOT = "entity_snapshot"
    PIPELINE_SNAPSHOT = "pipeline_snapshot"
    GROUP_START = "group_start"
    GROUP_STOP = "group_stop"
    GROUP_SNAPSHOT = "group_snapshot"
    ENTITY_REDO = "entity_redo"
    PIPELINE_REDO = "pipeline_redo"
    GROUP_REDO = "group_redo"
    PIPELINE_ENTER_MAINTENANCE = "pipeline_enter_maintenance"
    PIPELINE_EXIT_MAINTENANCE = "pipeline_exit_maintenance"
    QUERY_STUDIO = "query_studio"


QUERY_STUDIO_SQL_PREVIEW_LEN = 200


def preview_query_sql(query_sql, limit=QUERY_STUDIO_SQL_PREVIEW_LEN):
    """Return a truncated SQL preview for logs (never dump huge queries)."""
    if not query_sql:
        return ""
    text = str(query_sql)
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _nonempty(value):
    return bool(value and str(value).strip())


def require_query_studio_fields(task_type, agent_id, query_sql, saved_query_id=None):
    """Validate QUERY_STUDIO fields.

    agent_id is always required. Either query_sql or saved_query_id (or both)
    must be non-empty. Custom query: saved_query_id null + query_sql required.
    Saved query: saved_query_id set; query_sql is an optional snapshot of the
    SQL at save time (the UI always sends it, but missing SQL is still accepted).
    entity_ids / group_ids / with_snapshot are ignored for this task type.
    """
    if task_type != TaskType.QUERY_STUDIO:
        return
    if not _nonempty(agent_id):
        raise ValueError("query_studio requires a non-empty agent_id")
    if not _nonempty(query_sql) and not _nonempty(saved_query_id):
        raise ValueError("query_studio requires a non-empty query_sql or saved_query_id")


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    task_type = Column(Enum(TaskType), nullable=False)
    cron_expression = Column(String, nullable=False)
    pipeline_id = Column(String, nullable=False)
    entity_ids = Column(Text, nullable=True)
    group_ids = Column(Text, nullable=True)
    with_snapshot = Column(Boolean, default=False)
    snapshot_write_method = Column(String, nullable=False, default='UPSERT')
    agent_id = Column(String, nullable=True)
    query_sql = Column(Text, nullable=True)
    saved_query_id = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)
    command = Column(Text, nullable=False)
    cron_job_identifier = Column(String, nullable=False, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))
    last_run = Column(DateTime(timezone=True), nullable=True)
    next_run = Column(DateTime(timezone=True), nullable=True)
    last_successful_run = Column(DateTime(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_run_error_time = Column(DateTime(timezone=True), nullable=True)
    start_time = Column(String, nullable=True)
    timezone_name = Column(String, nullable=True)
    is_cron_expression = Column(Boolean, default=False, nullable=False)


class ExecutionMode(enum.Enum):
    ASYNC = "async"
    SYNC = "sync"


class ChainedJobEvent(Base):
    __tablename__ = "chained_job_events"

    id = Column(Integer, primary_key=True, index=True)
    parent_job_id = Column(Integer, ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)  # 0-based ordering
    task_type = Column(Enum(TaskType), nullable=False)
    pipeline_id = Column(String, nullable=False)
    entity_ids = Column(Text, nullable=True)   # JSON array
    group_ids = Column(Text, nullable=True)    # JSON array
    with_snapshot = Column(Boolean, default=False)
    snapshot_write_method = Column(String, nullable=False, default="UPSERT")
    agent_id = Column(String, nullable=True)
    query_sql = Column(Text, nullable=True)
    saved_query_id = Column(String, nullable=True)
    execution_mode = Column(Enum(ExecutionMode), nullable=False, default=ExecutionMode.ASYNC)
    webhook_timeout_seconds = Column(Integer, nullable=False, default=3600)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))


class TriggerFlow(Base):
    __tablename__ = "trigger_flows"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    platform_event = Column(String, nullable=True)
    secret_token = Column(String, nullable=False)
    last_triggered = Column(TIMESTAMP(timezone=True), nullable=True)
    last_successful_trigger = Column(TIMESTAMP(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_trigger_error_time = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))


class TriggerFlowEvent(Base):
    __tablename__ = "trigger_flow_events"

    id = Column(Integer, primary_key=True, index=True)
    trigger_flow_id = Column(Integer, ForeignKey("trigger_flows.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    task_type = Column(Enum(TaskType), nullable=False)
    pipeline_id = Column(String, nullable=False)
    entity_ids = Column(Text, nullable=True)    # JSON array
    group_ids = Column(Text, nullable=True)     # JSON array
    with_snapshot = Column(Boolean, default=False)
    snapshot_write_method = Column(String, nullable=False, default="UPSERT")
    agent_id = Column(String, nullable=True)
    query_sql = Column(Text, nullable=True)
    saved_query_id = Column(String, nullable=True)
    execution_mode = Column(Enum(ExecutionMode), nullable=False, default=ExecutionMode.ASYNC)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))


class Setting(Base):
    """Model for storing application settings as key-value pairs"""
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, unique=True, index=True)
    value = Column(String, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))


class TriggerFlowExecutionLog(Base):
    """Stores the last few execution logs for a trigger flow."""
    __tablename__ = "trigger_flow_execution_logs"

    id = Column(Integer, primary_key=True, index=True)
    trigger_flow_id = Column(Integer, ForeignKey("trigger_flows.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, nullable=False)  # "success" | "failed" | "queued"
    source = Column(String, nullable=True)   # "manual" | "platform_event" | "webhook"
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    triggered_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))
