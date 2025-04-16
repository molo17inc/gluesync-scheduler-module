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

from typing import List, Optional, Dict, Any, Union
from datetime import datetime
import pytz
import json
from enum import Enum
from pydantic import BaseModel, Field, validator, field_serializer, ConfigDict

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.models.models import TaskType


class DayOfWeek(str, Enum):
    """Days of the week for scheduling"""
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class ScheduleConfig(BaseModel):
    """User-friendly schedule configuration"""
    days_of_week: List[DayOfWeek] = Field(
        default_factory=list,
        description="Days of the week when the job should run (empty list means every day)",
        example=["monday", "wednesday", "friday"]
    )
    hour: int = Field(
        ..., 
        description="Hour of the day (0-23)", 
        example=8,
        ge=0,
        lt=24
    )
    minute: int = Field(
        ..., 
        description="Minute of the hour (0-59)", 
        example=30,
        ge=0,
        lt=60
    )
    
    @validator("days_of_week")
    def validate_days(cls, v):
        # Empty list means every day
        if not v:
            return v
        # Ensure all days are unique
        if len(v) != len(set(v)):
            raise ValueError("Days of week must be unique")
        return v


class JobBase(BaseModel):
    """Base model for job data with common fields"""
    name: str = Field(..., description="Name of the scheduled job", example="Daily entity backup")
    description: Optional[str] = Field(None, description="Optional description of the job's purpose", example="Create a daily snapshot of critical entities")
    task_type: TaskType = Field(..., description="Type of task to perform (use lowercase values in API requests):\n- entity_start: Start a specific entity within a pipeline\n- entity_stop: Stop a specific entity within a pipeline\n- pipeline_start: Start all entities in a pipeline\n- pipeline_stop: Stop all entities in a pipeline\n- entity_snapshot: Create a data snapshot of a specific entity\n- pipeline_snapshot: Create a data snapshot of all entities in a pipeline")
    schedule: Optional[ScheduleConfig] = Field(None, description="User-friendly schedule configuration")
    cron_expression: Optional[str] = Field(None, description="Cron expression for scheduling (e.g., '0 0 * * *' for daily at midnight). Not required if schedule is provided.", example="0 0 * * *")
    pipeline_id: str = Field(..., description="ID of the pipeline to operate on", example="pipeline-123")
    entity_ids: Optional[List[str]] = Field(None, description="List of entity IDs to operate on (required for entity operations)", example=["entity-456", "entity-789"])
    with_snapshot: bool = Field(False, description="Whether to include snapshot when starting entities")
    enabled: bool = Field(True, description="Whether the job is enabled and should be executed according to schedule")
    
    @validator("schedule", "cron_expression")
    def validate_schedule_options(cls, v, values):
        # Ensure either schedule or cron_expression is provided
        if "schedule" in values and "cron_expression" in values:
            if values["schedule"] is None and values["cron_expression"] is None:
                raise ValueError("Either schedule or cron_expression must be provided")
        return v


class JobCreate(JobBase):
    """Model for creating a new job (inherits all fields from JobBase)"""
    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "name": "Daily entity backup",
                "description": "Create a daily snapshot of critical entities",
                "task_type": "ENTITY_SNAPSHOT",
                "cron_expression": "0 0 * * *",
                "pipeline_id": "pipeline-123",
                "entity_ids": ["entity-456", "entity-789"],
                "with_snapshot": True,
                "enabled": True
            }
        }
    )


class JobUpdate(BaseModel):
    """Model for updating an existing job (all fields are optional)"""
    name: Optional[str] = Field(None, description="Updated name of the job", example="Updated daily entity backup")
    description: Optional[str] = Field(None, description="Updated description of the job", example="Updated description for the daily backup")
    task_type: Optional[TaskType] = Field(None, description="Updated type of task to perform (use lowercase values in API requests)")
    schedule: Optional[ScheduleConfig] = Field(None, description="Updated user-friendly schedule configuration")
    cron_expression: Optional[str] = Field(None, description="Updated cron expression. Not required if schedule is provided.", example="0 0 * * *")
    pipeline_id: Optional[str] = Field(None, description="Updated pipeline ID", example="pipeline-123")
    entity_ids: Optional[List[str]] = Field(None, description="List of entity IDs to operate on", example=["entity-456", "entity-789"])
    with_snapshot: Optional[bool] = Field(None, description="Updated snapshot setting")
    enabled: Optional[bool] = Field(None, description="Updated enabled status")
    
    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "name": "Updated daily entity backup",
                "description": "Updated description",
                "task_type": "pipeline_stop",
                "schedule": {
                    "days_of_week": ["monday", "wednesday", "friday"],
                    "hour": 8,
                    "minute": 30
                },
                "enabled": True
            }
        }
    )


class Job(JobBase):
    """Complete job model with all fields (used for responses)"""
    id: int = Field(..., description="Unique identifier for the job", example=1)
    cron_job_identifier: str = Field(..., description="Unique identifier used in the crontab", example="gluesync_job_1")
    created_at: datetime = Field(..., description="Timestamp when the job was created")
    updated_at: datetime = Field(..., description="Timestamp when the job was last updated")
    last_run: Optional[datetime] = Field(None, description="Timestamp of the last execution (null if never run)")
    last_successful_run: Optional[datetime] = Field(None, description="Timestamp of the last successful execution (null if never run)")
    last_error_message: Optional[str] = Field(None, description="Error message from the last failed execution (null if last execution was successful)")
    last_run_error_time: Optional[datetime] = Field(None, description="Timestamp of the last error (null if no errors occurred)")
    next_run: Optional[datetime] = Field(None, description="Timestamp of the next scheduled execution")
    command: str = Field(..., description="Command that will be executed by the cron job", example="python play_pause.py resync --pipeline pipeline-123 --entity entity-456")
    schedule_days: Optional[List[str]] = Field(None, description="Array of days when the job is scheduled to run (e.g., ['monday', 'wednesday', 'friday'])")
    start_time: Optional[str] = Field(None, description="Current time with timezone information when the job data was retrieved", example="2025-04-14T23:19:46+0200")
    
    @validator('entity_ids', pre=True)
    def parse_entity_ids(cls, v):
        """Parse entity_ids from JSON string to list if it's a string"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v
    
    model_config = ConfigDict(
        from_attributes = True,
        json_schema_extra = {
            "example": {
                "id": 1,
                "name": "Daily entity backup",
                "description": "Create a daily snapshot of critical entities",
                "task_type": "ENTITY_SNAPSHOT",
                "cron_expression": "0 0 * * *",
                "pipeline_id": "pipeline-123",
                "entity_ids": ["entity-456", "entity-789"],
                "with_snapshot": True,
                "enabled": True,
                "command": "python3 play_pause.py resync --pipeline pipeline-123 --entity entity-456,entity-789",
                "cron_job_identifier": "gluesync_job_1",
                "created_at": "2025-03-20T10:00:00+02:00",
                "updated_at": "2025-03-20T10:00:00+02:00",
                "last_run": "2025-03-20T00:00:00+02:00",
                "last_successful_run": "2025-03-20T00:00:00+02:00",
                "last_error_message": None,
                "last_run_error_time": None,
                "next_run": "2025-03-21T00:00:00+02:00",
                "schedule_days": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
                "start_time": "2025-04-14T23:19:46+0200"
            }
        }
    )
    
    @field_serializer('created_at', 'updated_at', 'last_run', 'last_successful_run', 'last_run_error_time', 'next_run')
    def serialize_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        # Ensure datetime has timezone information
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.UTC)
        return dt.isoformat()


class JobList(BaseModel):
    """Model for paginated list of jobs"""
    items: List[Job] = Field(..., description="List of job objects")
    total: int = Field(..., description="Total number of jobs (without pagination)")
    
    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "items": [
                    {
                        "id": 1,
                        "name": "Daily entity backup",
                        "description": "Create a daily snapshot of critical entities",
                        "task_type": "ENTITY_SNAPSHOT",
                        "cron_expression": "0 0 * * *",
                        "pipeline_id": "pipeline-123",
                        "entity_ids": ["entity-456", "entity-789"],
                        "with_snapshot": True,
                        "enabled": True,
                        "command": "python3 play_pause.py resync --pipeline pipeline-123 --entity entity-456,entity-789",
                        "cron_job_identifier": "gluesync_job_1",
                        "created_at": "2025-03-20T10:00:00Z",
                        "updated_at": "2025-03-20T10:00:00Z",
                        "last_run": "2025-03-20T00:00:00Z",
                        "last_successful_run": "2025-03-20T00:00:00Z",
                        "last_error_message": None,
                        "last_run_error_time": None,
                        "next_run": "2025-03-21T00:00:00Z"
                    }
                ],
                "total": 1
            }
        }
    )


class ErrorResponse(BaseModel):
    """Model for error responses"""
    detail: str = Field(..., description="Error message with details about the problem")
    
    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "detail": "Job with ID 123 not found"
            }
        }
    )


class OperationResponse(BaseModel):
    """Model for operation responses"""
    success: bool = Field(..., description="Whether the operation was successful")
    message: str = Field(..., description="Message describing the result of the operation")
    data: Optional[Dict[str, Any]] = Field(None, description="Optional data returned by the operation")
