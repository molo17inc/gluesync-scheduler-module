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
import os
from enum import Enum
from pydantic import BaseModel, Field, validator, field_serializer, ConfigDict

from gluesync_scheduler.models.models import TaskType, ExecutionMode
from gluesync_scheduler.core.timezone_utils import get_env_timezone


class ChainedEventMode(str, Enum):
    """Execution mode for a chained event"""
    ASYNC = "async"   # fire-and-forget; don't wait for completion
    SYNC = "sync"     # register a corehub webhook and wait for callback before proceeding


class ChainedEventBase(BaseModel):
    """Fields shared by create and response schemas for chained events"""
    task_type: TaskType = Field(..., description="Type of task to perform")
    pipeline_id: str = Field(..., description="Pipeline ID to operate on")
    entity_ids: Optional[List[str]] = Field(None, description="Entity IDs (required for entity operations)")
    group_ids: Optional[List[str]] = Field(None, description="Group IDs (required for group operations)")
    with_snapshot: bool = Field(False, description="Whether to include a snapshot")
    snapshot_write_method: str = Field("UPSERT", description="Snapshot write method: UPSERT or INSERT", pattern="^(UPSERT|INSERT)$")
    execution_mode: ChainedEventMode = Field(ChainedEventMode.ASYNC, description="async: fire-and-forget; sync: wait for corehub webhook callback before next event")


class ChainedEventCreate(ChainedEventBase):
    """Schema used when creating chained events (no extra fields)"""
    pass


class ChainedEventResponse(ChainedEventBase):
    """Schema returned by the API for a chained event"""
    id: int
    position: int
    parent_job_id: int

    model_config = ConfigDict(from_attributes=True)


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
    task_type: TaskType = Field(..., description="Type of task to perform (use lowercase values in API requests):\n- entity_start: Start a specific entity within a pipeline\n- entity_stop: Stop a specific entity within a pipeline\n- pipeline_start: Start all entities in a pipeline\n- pipeline_stop: Stop all entities in a pipeline\n- entity_snapshot: Create a data snapshot of a specific entity\n- pipeline_snapshot: Create a data snapshot of all entities in a pipeline\n- group_start: Start all entities within specific groups\n- group_stop: Stop all entities within specific groups\n- group_snapshot: Create a data snapshot of all entities within specific groups\n- entity_redo: Trigger redo (snapshot + CDC restart) for a specific entity\n- pipeline_redo: Trigger redo (snapshot + CDC restart) for all entities in a pipeline\n- group_redo: Trigger redo (snapshot + CDC restart) for all entities within specific groups\n- pipeline_enter_maintenance: Enter maintenance mode for a pipeline\n- pipeline_exit_maintenance: Exit maintenance mode for a pipeline")
    schedule: Optional[ScheduleConfig] = Field(None, description="User-friendly schedule configuration")
    cron_expression: Optional[str] = Field(None, description="Cron expression for scheduling (e.g., '0 0 * * *' for daily at midnight). Not required if schedule is provided.", example="0 0 * * *")
    pipeline_id: str = Field(..., description="ID of the pipeline to operate on", example="pipeline-123")
    entity_ids: Optional[List[str]] = Field(None, description="List of entity IDs to operate on (required for entity operations)", example=["entity-456", "entity-789"])
    group_ids: Optional[List[str]] = Field(None, description="List of group IDs to operate on (required for group operations)", example=["group-123", "group-456"])
    with_snapshot: bool = Field(False, description="Whether to include snapshot when starting entities")
    snapshot_write_method: str = Field("UPSERT", description="Write method for snapshot operations (UPSERT or INSERT)", pattern="^(UPSERT|INSERT)$")
    enabled: bool = Field(True, description="Whether the job is enabled and should be executed according to schedule")
    is_cron_expression: bool = Field(False, description="Whether the job was created with a cron expression (true) or schedule configuration (false)")
    chained_events: Optional[List[ChainedEventCreate]] = Field(
        None,
        description="Ordered list of additional tasks to fire after this job completes"
    )

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
    group_ids: Optional[List[str]] = Field(None, description="List of group IDs to operate on", example=["group-123", "group-456"])
    with_snapshot: Optional[bool] = Field(None, description="Updated snapshot setting")
    snapshot_write_method: Optional[str] = Field(None, description="Updated write method for snapshot operations (UPSERT or INSERT)", pattern="^(UPSERT|INSERT)$")
    enabled: Optional[bool] = Field(None, description="Updated enabled status")
    is_cron_expression: Optional[bool] = Field(None, description="Whether the job was created with a cron expression (true) or schedule configuration (false)")
    chained_events: Optional[List[ChainedEventCreate]] = Field(None, description="Replace all chained events with this list (pass empty list to clear)")

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
                "snapshot_write_method": "INSERT",
                "enabled": True,
                "is_cron_expression": False
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
    schedule_days: List[str] = Field(default_factory=list, description="Array of days when the job is scheduled to run (e.g., ['monday', 'wednesday', 'friday'])")
    # Explicitly re-define snapshot_write_method to ensure it's included in the response
    snapshot_write_method: str = Field("UPSERT", description="Write method for snapshot operations (UPSERT or INSERT)", pattern="^(UPSERT|INSERT)$")
    is_cron_expression: bool = Field(False, description="Whether the job was created with a cron expression (true) or schedule configuration (false)")
    
    # Ensure schedule_days is always populated
    @validator('schedule_days', always=True)
    def extract_days_from_cron(cls, v, values):
        """Extract days from cron expression if schedule_days is empty"""
        # If we already have schedule_days, use them
        if v and isinstance(v, list):
            return v
            
        # If we have a cron expression, extract days from it
        cron = values.get('cron_expression')
        if not cron:
            return []  # Empty list if no cron expression
            
        try:
            # Parse cron expression (minute hour day_of_month month day_of_week)
            parts = cron.split()
            if len(parts) != 5:
                return []  # Invalid cron format
                
            dow_part = parts[4]  # 5th part is day of week
            
            # Map from cron day nums (0-6) to day names
            day_map = {
                "0": "sunday", "1": "monday", "2": "tuesday", "3": "wednesday",
                "4": "thursday", "5": "friday", "6": "saturday"
            }
            
            # Create a detailed log string to help with debugging
            log_msg = f"Extracting days from cron expression: {cron}, day of week part: {dow_part}"
            
            # Handle different dow formats
            if dow_part == "*":
                # All days
                return list(day_map.values())
            
            # Extract specific days
            result = []
            for day in dow_part.split(','):
                # Handle ranges (e.g., 1-5)
                if '-' in day:
                    start, end = day.split('-')
                    for d in range(int(start), int(end) + 1):
                        if str(d) in day_map:
                            result.append(day_map[str(d)])
                # Single day
                elif day in day_map:
                    result.append(day_map[day])
                    
            # Ensure we have at least one day in the result if dow_part is not '*'
            # but we couldn't extract any days (e.g., due to parsing issues)
            if not result and dow_part != "*" and dow_part.isdigit():
                day_num = int(dow_part)
                if 0 <= day_num <= 6 and str(day_num) in day_map:
                    result.append(day_map[str(day_num)])
                    
            return result
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Error extracting days from cron: {e}")
            return []  # Return empty array on error
    
    # Calculate next_run based on cron_expression
    @validator('next_run', always=True)
    def calculate_next_run(cls, v, values):
        """Calculate next run time based on cron expression"""
        # If next_run is already set, use it
        if v is not None:
            return v
            
        # Calculate based on cron expression
        cron = values.get('cron_expression')
        if not cron:
            return None  # No cron, no next_run
            
        try:
            from datetime import datetime, timezone
            import pytz
            from croniter import croniter
            
            # Get current time in the configured timezone
            tz = pytz.timezone(get_env_timezone('UTC'))
            now = datetime.now(tz)
            
            # Use croniter to calculate the next run time
            cron_iter = croniter(cron, now)
            next_datetime = cron_iter.get_next(datetime)
            
            # Make sure the datetime has the correct timezone
            if next_datetime.tzinfo is None:
                next_datetime = tz.localize(next_datetime)
            elif str(next_datetime.tzinfo) != str(tz):
                # Convert to the configured timezone
                next_datetime = next_datetime.astimezone(tz)
            
            return next_datetime
        except Exception as e:
            # If croniter is not available or other error, return current time + 1 day as fallback
            # This ensures we at least have a value for next_run
            from datetime import datetime, timezone, timedelta
            return datetime.now(timezone.utc) + timedelta(days=1)
    start_time: Optional[str] = Field(None, description="Scheduled start time for the job in the job's timezone", example="2025-04-14T23:19:46+02:00")
    timezone_name: Optional[str] = Field(None, description="Name of the timezone used for scheduling", example="Asia/Tokyo")
    chained_events: List[ChainedEventResponse] = Field(default_factory=list, description="Ordered chained events for this job")
    
    @validator('entity_ids', pre=True)
    def parse_entity_ids(cls, v):
        """Parse entity_ids from JSON string to list if it's a string"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v
    
    @validator('group_ids', pre=True)
    def parse_group_ids(cls, v):
        """Parse group_ids from JSON string to list if it's a string"""
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
                "snapshot_write_method": "UPSERT",
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
    def serialize_datetime(self, dt: Optional[datetime], info) -> Optional[str]:
        if dt is None:
            return None
        
        # Get the job's timezone or use settings timezone as fallback
        tz = None
        if hasattr(self, 'timezone_name') and self.timezone_name:
            try:
                tz = pytz.timezone(self.timezone_name)
            except:
                pass
        
        # Fallback to settings timezone if job doesn't have one
        if tz is None:
            try:
                tz = pytz.timezone(get_env_timezone('UTC'))
            except:
                tz = pytz.UTC
        
        # Ensure datetime has timezone information
        if dt.tzinfo is None:
            # If no timezone info, assume it's in UTC
            dt = pytz.UTC.localize(dt)
        
        # Convert to the job's timezone
        dt_in_timezone = dt.astimezone(tz)
        
        # Return consistent ISO8601 format with colon in offset for all fields
        return dt_in_timezone.isoformat()
    
    @field_serializer('start_time')
    def serialize_start_time(self, start_time: Optional[str], info) -> Optional[str]:
        if start_time is None:
            return None
        
        # If it's already a properly formatted string with timezone, parse and reformat
        try:
            # Parse the datetime string
            if isinstance(start_time, str):
                # Handle various formats
                dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            elif isinstance(start_time, datetime):
                dt = start_time
            else:
                return start_time
            
            # Get the job's timezone
            tz = None
            if hasattr(self, 'timezone_name') and self.timezone_name:
                try:
                    tz = pytz.timezone(self.timezone_name)
                except:
                    pass
            
            # Fallback to settings timezone
            if tz is None:
                try:
                    tz = pytz.timezone(get_env_timezone('UTC'))
                except:
                    tz = pytz.UTC
            
            # Ensure datetime has timezone information
            if dt.tzinfo is None:
                dt = pytz.UTC.localize(dt)
            
            # Convert to the job's timezone
            dt_in_timezone = dt.astimezone(tz)
            
            # Return consistent ISO8601 format
            return dt_in_timezone.isoformat()
        except Exception as e:
            # If we can't parse it, return as is
            return start_time


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
                        "snapshot_write_method": "UPSERT",
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


class SettingBase(BaseModel):
    """Base model for setting fields"""
    key: str = Field(..., description="Unique key for the setting", example="timezone")
    value: Optional[str] = Field(None, description="Value of the setting", example="America/New_York")
    description: Optional[str] = Field(None, description="Description of what the setting controls", example="Timezone used for scheduling jobs")


class SettingCreate(SettingBase):
    """Model for creating a new setting"""
    pass


class SettingUpdate(BaseModel):
    """Model for updating an existing setting"""
    value: Optional[str] = Field(..., description="New value for the setting", example="America/New_York")
    description: Optional[str] = Field(None, description="Updated description")


class Setting(SettingBase):
    """Complete setting model with all fields (used for responses)"""
    id: int = Field(..., description="Unique identifier for the setting")
    created_at: datetime = Field(..., description="Timestamp when the setting was created")
    updated_at: datetime = Field(..., description="Timestamp when the setting was last updated")
    
    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "id": 1,
                "key": "timezone",
                "value": "America/New_York",
                "description": "Timezone used for scheduling jobs",
                "created_at": "2025-05-09T10:00:00Z",
                "updated_at": "2025-05-09T14:30:00Z"
            }
        }
    )


class SettingsList(BaseModel):
    """Model for a list of settings"""
    items: List[Setting] = Field(..., description="List of setting objects")
    total: int = Field(..., description="Total number of settings")
    
    model_config = ConfigDict(
        json_schema_extra = {
            "example": {
                "items": [
                    {
                        "id": 1,
                        "key": "timezone",
                        "value": "America/New_York",
                        "description": "Timezone used for scheduling jobs",
                        "created_at": "2025-05-09T10:00:00Z",
                        "updated_at": "2025-05-09T14:30:00Z"
                    }
                ],
                "total": 1
            }
        }
    )
