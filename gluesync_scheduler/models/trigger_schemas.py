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

import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import pytz
from pydantic import BaseModel, ConfigDict, Field, field_serializer, validator

from gluesync_scheduler.models.models import TaskType
from gluesync_scheduler.core.timezone_utils import get_env_timezone


# ---------------------------------------------------------------------------
# Shared sub-schemas
# ---------------------------------------------------------------------------

class TriggerEventMode(str, Enum):
    ASYNC = "async"
    SYNC = "sync"


class TriggerEventBase(BaseModel):
    task_type: TaskType = Field(..., description="Type of task to perform")
    pipeline_id: str = Field(..., description="Pipeline ID to operate on")
    entity_ids: Optional[List[str]] = Field(None, description="Entity IDs (required for entity operations)")
    group_ids: Optional[List[str]] = Field(None, description="Group IDs (required for group operations)")
    with_snapshot: bool = Field(False, description="Whether to include a snapshot")
    snapshot_write_method: str = Field(
        "UPSERT",
        description="Snapshot write method: UPSERT or INSERT",
        pattern="^(UPSERT|INSERT)$",
    )
    execution_mode: TriggerEventMode = Field(
        TriggerEventMode.ASYNC,
        description="async: fire-and-forget; sync: wait for corehub webhook callback before next event",
    )


class TriggerEventCreate(TriggerEventBase):
    """Schema used when creating trigger events (no extra fields)."""
    pass


class TriggerEventResponse(TriggerEventBase):
    """Schema returned by the API for a trigger event."""
    id: int
    position: int
    trigger_flow_id: int

    @validator("entity_ids", pre=True)
    def parse_entity_ids(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v

    @validator("group_ids", pre=True)
    def parse_group_ids(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# TriggerFlow schemas
# ---------------------------------------------------------------------------

class TriggerFlowBase(BaseModel):
    name: str = Field(..., description="Human-readable name for this trigger flow", example="post-deploy-resync")
    description: Optional[str] = Field(None, description="Optional description", example="Fired after each production deploy")
    enabled: bool = Field(True, description="Whether this trigger flow is active")


class TriggerFlowCreate(TriggerFlowBase):
    events: List[TriggerEventCreate] = Field(
        ...,
        min_length=1,
        description="Ordered list of actions to execute when this flow is fired",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "post-deploy-resync",
                "description": "Triggered after each production deploy",
                "enabled": True,
                "events": [
                    {
                        "task_type": "pipeline_stop",
                        "pipeline_id": "prod-pipeline",
                        "execution_mode": "sync",
                    },
                    {
                        "task_type": "pipeline_snapshot",
                        "pipeline_id": "prod-pipeline",
                        "with_snapshot": True,
                        "snapshot_write_method": "UPSERT",
                        "execution_mode": "sync",
                    },
                    {
                        "task_type": "pipeline_start",
                        "pipeline_id": "prod-pipeline",
                        "execution_mode": "async",
                    },
                ],
            }
        }
    )


class TriggerFlowUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""
    name: Optional[str] = Field(None, description="Updated name")
    description: Optional[str] = Field(None, description="Updated description")
    enabled: Optional[bool] = Field(None, description="Enable or disable the flow")
    events: Optional[List[TriggerEventCreate]] = Field(
        None,
        description="Replace all events with this list (pass empty list to clear — at least 1 required on create)",
    )


class TriggerFlowResponse(TriggerFlowBase):
    """Full TriggerFlow returned by the API."""
    id: int
    # secret_token is intentionally omitted here — only returned on create/regenerate
    trigger_url: str = Field(..., description="Stable URL to POST for firing this flow")
    events: List[TriggerEventResponse] = Field(default_factory=list)
    last_triggered: Optional[datetime] = None
    last_successful_trigger: Optional[datetime] = None
    last_error_message: Optional[str] = None
    last_trigger_error_time: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "last_triggered", "last_successful_trigger",
        "last_trigger_error_time", "created_at", "updated_at",
    )
    def serialize_dt(self, dt: Optional[datetime], info) -> Optional[str]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        try:
            tz = pytz.timezone(get_env_timezone("UTC"))
            dt = dt.astimezone(tz)
        except Exception:
            pass
        return dt.isoformat()

    model_config = ConfigDict(from_attributes=True)


class TriggerFlowCreateResponse(TriggerFlowResponse):
    """Returned only on creation and token regeneration — includes the plaintext secret."""
    secret_token: str = Field(..., description="Secret token (shown once — store it safely)")


class TriggerFlowList(BaseModel):
    items: List[TriggerFlowResponse]
    total: int


# ---------------------------------------------------------------------------
# Fire endpoint schemas
# ---------------------------------------------------------------------------

class FireStatus(str, Enum):
    QUEUED = "queued"
    COMPLETED = "completed"
    FAILED = "failed"


class FireResponse(BaseModel):
    trigger_flow_id: int
    triggered_at: str
    status: FireStatus
    events_count: int
    message: str
    detail: Optional[Dict[str, Any]] = None
