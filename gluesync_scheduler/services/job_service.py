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
import logging
import os
import requests
import uuid
import pytz
from croniter import croniter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any, Union

from fastapi import HTTPException, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from gluesync_scheduler.models.models import ScheduledJob, TaskType, Setting, ChainedJobEvent, ExecutionMode
from gluesync_scheduler.models.schemas import JobCreate, JobUpdate, Job, ScheduleConfig, ChainedEventResponse
from gluesync_scheduler.services.scheduler_service import scheduler_service
from gluesync_scheduler.core.timezone_utils import get_env_timezone

logger = logging.getLogger(__name__)

class JobService:
    """Service for managing scheduled jobs"""

    def __init__(self, db: Session):
        self.db = db
        # Use the singleton scheduler service instance
        self.scheduler_service = scheduler_service
    
    def _get_configured_timezone(self) -> str:
        """Get the current configured timezone from database or environment.
        
        Returns:
            The timezone string (e.g., 'Asia/Taipei', 'UTC')
        """
        # First try to get from database
        timezone_setting = self.db.query(Setting).filter(Setting.key == "timezone").first()
        if timezone_setting and timezone_setting.value:
            timezone_name = timezone_setting.value
            logger.info(f"Retrieved timezone from database: {timezone_name}")
        else:
            # Fall back to environment variable
            timezone_name = get_env_timezone('UTC')
            logger.info(f"Retrieved timezone from environment: {timezone_name}")
        
        # Validate the timezone
        try:
            pytz.timezone(timezone_name)
            return timezone_name
        except Exception as e:
            logger.error(f"Invalid timezone configured: {timezone_name}. Error: {str(e)}")
            logger.warning("Falling back to UTC timezone")
            return 'UTC'
        
    def _extract_days_from_cron(self, cron_expression: str) -> List[str]:
        """Extract days of week from cron expression and convert to day names
        
        Args:
            cron_expression: Cron expression to parse
            
        Returns:
            List of day names (e.g., ['monday', 'wednesday', 'friday'])
        """
        if not cron_expression:
            return []  # Empty array if no cron expression
            
        try:
            # Parse the cron expression
            # Format: minute hour day_of_month month day_of_week
            parts = cron_expression.split()
            if len(parts) != 5:
                logger.warning(f"Invalid cron expression format: {cron_expression}")
                return []
                
            day_of_week_part = parts[4]  # 5th part is day of week
            
            # Convert cron dow format (0-6) to day names
            day_names = {
                "0": "sunday", "1": "monday", "2": "tuesday", "3": "wednesday",
                "4": "thursday", "5": "friday", "6": "saturday"
            }
            
            # Handle different formats of day_of_week_part
            if day_of_week_part == "*":
                # All days
                return list(day_names.values())
            else:
                # Specific days
                days = []
                for day in day_of_week_part.split(','):
                    # Handle ranges like 1-5
                    if '-' in day:
                        start, end = day.split('-')
                        for d in range(int(start), int(end) + 1):
                            if str(d) in day_names:
                                days.append(day_names[str(d)])
                    # Single day
                    elif day in day_names:
                        days.append(day_names[day])
                return days
        except Exception as e:
            logger.error(f"Error parsing cron expression: {e}")
            return []  # Return empty array on error

    def _apply_timezone_to_job(self, job_model: Job, db_job: ScheduledJob) -> Job:
        """
        Apply timezone information to job dates
        
        Args:
            job_model: The Pydantic model to update
            db_job: The database job with timezone information
            
        Returns:
            Updated job model with proper timezone info
        """
        # Add schedule days
        job_model.schedule_days = self._extract_days_from_cron(db_job.cron_expression)
        
        # If we have timezone information and dates, ensure they're properly formatted
        if hasattr(db_job, 'timezone_name') and db_job.timezone_name:
            # Get the timezone
            try:
                tz = pytz.timezone(db_job.timezone_name)
                
                # Process next_run/start_time if it exists
                if job_model.next_run and isinstance(job_model.next_run, str):
                    # Parse the datetime string
                    try:
                        # If it already has timezone info, just ensure it's in the right format
                        if '+' in job_model.next_run or 'Z' in job_model.next_run:
                            dt = datetime.fromisoformat(job_model.next_run.replace('Z', '+00:00'))
                            # Convert to the configured timezone
                            dt = dt.astimezone(tz)
                            # Format with timezone info
                            job_model.next_run = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
                    except Exception as e:
                        logger.warning(f"Error processing next_run timezone: {e}")
                        
                # Same for start_time
                if job_model.start_time and isinstance(job_model.start_time, str):
                    try:
                        if '+' in job_model.start_time or 'Z' in job_model.start_time:
                            dt = datetime.fromisoformat(job_model.start_time.replace('Z', '+00:00'))
                            dt = dt.astimezone(tz)
                            job_model.start_time = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
                    except Exception as e:
                        logger.warning(f"Error processing start_time timezone: {e}")
            except Exception as e:
                logger.warning(f"Error applying timezone to job: {e}")
                
        return job_model
    
    def get_jobs(
        self, 
        skip: int = 0, 
        limit: int = 100, 
        task_type: Optional[TaskType] = None,
        enabled: Optional[bool] = None
    ) -> Tuple[List[Job], int]:
        """
        Get a list of jobs with optional filtering
        
        Args:
            skip: Number of records to skip (for pagination)
            limit: Maximum number of records to return
            task_type: Filter by task type
            enabled: Filter by enabled status
            
        Returns:
            Tuple of (list of jobs, total count)
        """
        query = self.db.query(ScheduledJob)
        
        # Apply filters if provided
        filters = []
        if task_type is not None:
            filters.append(ScheduledJob.task_type == task_type)
        if enabled is not None:
            filters.append(ScheduledJob.enabled == enabled)
            
        if filters:
            query = query.filter(and_(*filters))
            
        # Get total count before pagination
        total = query.count()
        
        # Apply pagination
        jobs = query.offset(skip).limit(limit).all()

        # Batch-load chained events for all jobs in a single query (avoids N+1)
        job_ids = [job.id for job in jobs]
        if job_ids:
            all_events = (
                self.db.query(ChainedJobEvent)
                .filter(ChainedJobEvent.parent_job_id.in_(job_ids))
                .order_by(ChainedJobEvent.parent_job_id, ChainedJobEvent.position)
                .all()
            )
            events_by_job: Dict[int, list] = {}
            for row in all_events:
                events_by_job.setdefault(row.parent_job_id, []).append(row)
        else:
            events_by_job = {}

        # Resolve the configured timezone once per request (avoids per-job lookups)
        tz_name = self._get_configured_timezone()
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.UTC
        now = datetime.now(tz)

        # Request-scoped caches keyed by cron expression (many jobs share expressions)
        schedule_days_cache: Dict[str, List[str]] = {}
        next_run_cache: Dict[str, Optional[datetime]] = {}

        # Convert to Pydantic models with timezone handling
        job_responses = []
        backfilled = False
        for job in jobs:
            job_model = Job.from_orm(job)

            cron = job.cron_expression
            # schedule_days: compute once per unique cron expression
            if cron not in schedule_days_cache:
                schedule_days_cache[cron] = self._extract_days_from_cron(cron)
            job_model.schedule_days = schedule_days_cache[cron]

            # next_run: prefer the persisted DB value; only compute (and backfill)
            # legacy rows where it was never stored.
            if job.next_run is None and cron:
                if cron not in next_run_cache:
                    next_run_cache[cron] = self._compute_next_run(cron, tz, now)
                computed = next_run_cache[cron]
                if computed is not None:
                    job_model.next_run = computed
                    job.next_run = computed  # backfill so future reads are cheap
                    backfilled = True

            job_model.chained_events = _rows_to_chained_responses(events_by_job.get(job.id, []))
            job_responses.append(job_model)

        # Persist any lazily backfilled next_run values in a single commit
        if backfilled:
            try:
                self.db.commit()
            except Exception as e:
                logger.warning(f"Failed to backfill next_run values: {e}")
                self.db.rollback()

        return job_responses, total

    def _compute_next_run(
        self,
        cron_expression: str,
        tz: "pytz.BaseTzInfo",
        now: datetime,
    ) -> Optional[datetime]:
        """Compute the next run datetime for a cron expression in the given timezone."""
        try:
            cron_iter = croniter(cron_expression, now)
            nxt = cron_iter.get_next(datetime)
            if nxt.tzinfo is None:
                nxt = tz.localize(nxt)
            elif str(nxt.tzinfo) != str(tz):
                nxt = nxt.astimezone(tz)
            return nxt
        except Exception as e:
            logger.warning(f"Failed to compute next_run for '{cron_expression}': {e}")
            return None

    def get_job_by_id(self, job_id: int) -> Job:
        """
        Get a job by its ID
        """
        job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
        job_model = Job.from_orm(job)
        job_model = self._apply_timezone_to_job(job_model, job)
        job_model.chained_events = _load_chained_events(self.db, job_id)
        return job_model

    def get_job(self, job_id: int) -> Job:
        """
        Alias for get_job_by_id for compatibility with API router
        
        Args:
            job_id: The job ID to retrieve
            
        Returns:
            The job if found
            
        Raises:
            HTTPException: If job not found
        """
        return self.get_job_by_id(job_id)

    def create_job(self, job_data: JobCreate) -> Job:
        """
        Create a new scheduled job
        
        Args:
            job_data: The job data to create
            
        Returns:
            The created job
            
        Raises:
            HTTPException: If there's an error creating the job
        """
        try:
            # Validate that cron_expression is provided (required by database schema)
            if not job_data.cron_expression:
                # If no cron_expression, check if we have a schedule to convert to cron
                if job_data.schedule:
                    # Convert schedule to cron expression
                    try:
                        # Log the received schedule for debugging
                        logger.info(f"Schedule data received: {job_data.schedule}")
                        
                        # Access schedule components directly
                        schedule_dict = job_data.schedule.dict()
                        minute = schedule_dict.get('minute')
                        hour = schedule_dict.get('hour')
                        days_of_week = schedule_dict.get('days_of_week', [])
                        
                        # Validate required fields
                        if minute is None or hour is None:
                            raise ValueError("Schedule must include both hour and minute")
                            
                        logger.info(f"Schedule components: hour={hour}, minute={minute}, days={days_of_week}")
                        
                        # Convert days of week to cron format (0-6, where 0 is Sunday)
                        dow_map = {
                            "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
                            "thursday": 4, "friday": 5, "saturday": 6
                        }
                        
                        # Handle days of week (could be strings or DayOfWeek enums)
                        if days_of_week:
                            dow_values = []
                            for day in days_of_week:
                                # Handle both string and enum values
                                day_str = day.lower() if isinstance(day, str) else day.value.lower()
                                if day_str in dow_map:
                                    dow_values.append(str(dow_map[day_str]))
                        else:
                            dow_values = ["*"]  # All days
                            
                        dow_string = ",".join(dow_values)
                        
                        # Create cron expression (minute hour * * day_of_week)
                        cron_expression = f"{minute} {hour} * * {dow_string}"
                        job_data.cron_expression = cron_expression
                        
                        logger.info(f"Generated cron expression from schedule: {job_data.cron_expression}")
                    except Exception as e:
                        logger.error(f"Failed to convert schedule to cron expression: {e}")
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Failed to convert schedule to cron expression: {str(e)}"
                        )
                else:
                    # Neither cron_expression nor schedule provided
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="A cron expression or schedule is required for job creation"
                    )
            # Calculate the next run time based on cron expression for start_time
            next_run_time = None
            next_run_dt = None
            if job_data.cron_expression:
                try:
                    from croniter import croniter
                    # Get the current configured timezone from database
                    current_timezone = self._get_configured_timezone()
                    tz = pytz.timezone(current_timezone)
                    logger.info(f"Using timezone for calculation: {current_timezone}")
                    
                    now = datetime.now(tz)
                    
                    # Use croniter to calculate the next run time
                    cron_iter = croniter(job_data.cron_expression, now)
                    next_run_datetime = cron_iter.get_next(datetime)
                    
                    # Make sure the datetime has the correct timezone
                    if next_run_datetime.tzinfo is None:
                        next_run_datetime = tz.localize(next_run_datetime)
                    elif str(next_run_datetime.tzinfo) != str(tz):
                        # Convert to the configured timezone
                        next_run_datetime = next_run_datetime.astimezone(tz)
                    
                    # Format the start_time in the required format with explicit timezone info
                    next_run_time = next_run_datetime.strftime("%Y-%m-%dT%H:%M:%S%z")
                    # Keep the tz-aware datetime to persist into the next_run DB column
                    next_run_dt = next_run_datetime
                    # Store the timezone name as well for reference
                    next_run_tz = current_timezone
                    logger.info(f"Calculated next run time: {next_run_time} in timezone {next_run_tz}")
                except Exception as e:
                    logger.error(f"Error calculating next run time: {e}")
                    # If calculation fails, we'll leave start_time as None
            
            # Determine if job was created with cron expression or schedule
            is_cron_expression = bool(job_data.cron_expression and not job_data.schedule)
            
            # Validate group jobs have group_ids
            if job_data.task_type in [TaskType.GROUP_START, TaskType.GROUP_STOP, TaskType.GROUP_SNAPSHOT, TaskType.GROUP_REDO]:
                if not job_data.group_ids or len(job_data.group_ids) == 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Task type {job_data.task_type} requires group_ids to be provided"
                    )
                logger.info(f"Creating group job with {len(job_data.group_ids)} groups: {job_data.group_ids}")
            
            # Create the database record
            db_job = ScheduledJob(
                name=job_data.name,
                description=job_data.description,
                task_type=job_data.task_type,
                cron_expression=job_data.cron_expression,
                pipeline_id=job_data.pipeline_id,
                entity_ids=json.dumps(job_data.entity_ids) if job_data.entity_ids else None,
                group_ids=json.dumps(job_data.group_ids) if job_data.group_ids else None,
                with_snapshot=job_data.with_snapshot,
                snapshot_write_method=job_data.snapshot_write_method,
                enabled=job_data.enabled,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                # Set a placeholder command to satisfy NOT NULL constraint
                command="pending",
                # Set start_time to the calculated next run time
                start_time=next_run_time,
                # Persist the next run time so list views don't recompute it per request
                next_run=next_run_dt,
                # Always store the configured timezone name
                timezone_name=self._get_configured_timezone(),
                # Set the flag to track if job was created with cron expression
                is_cron_expression=is_cron_expression
            )
            
            # Generate a unique identifier for the cron job
            db_job.cron_job_identifier = f"gluesync_job_{uuid.uuid4().hex[:8]}"
            
            # Add to database and refresh to get the ID
            self.db.add(db_job)
            self.db.commit()
            self.db.refresh(db_job)
            
            # Create the actual scheduled job if enabled
            if db_job.enabled:
                job_id = self.scheduler_service.create_job(db_job)
                
                # Update the job_id in the database
                db_job.command = f"Scheduled job ID: {job_id}"
                self.db.commit()
                self.db.refresh(db_job)
            
            # Persist chained events
            if job_data.chained_events:
                created_events = []
                for pos, ce in enumerate(job_data.chained_events):
                    chained = ChainedJobEvent(
                        parent_job_id=db_job.id,
                        position=pos,
                        task_type=ce.task_type,
                        pipeline_id=ce.pipeline_id,
                        entity_ids=json.dumps(ce.entity_ids) if ce.entity_ids else None,
                        group_ids=json.dumps(ce.group_ids) if ce.group_ids else None,
                        with_snapshot=ce.with_snapshot,
                        snapshot_write_method=ce.snapshot_write_method,
                        execution_mode=ExecutionMode(ce.execution_mode.value),
                        webhook_timeout_seconds=ce.webhook_timeout_seconds,
                    )
                    self.db.add(chained)
                self.db.commit()
                # Refresh to get the auto-generated IDs
                created_events = (
                    self.db.query(ChainedJobEvent)
                    .filter(ChainedJobEvent.parent_job_id == db_job.id)
                    .order_by(ChainedJobEvent.position)
                    .all()
                )
                # Register persistent webhooks for SYNC events
                from gluesync_scheduler.services.chain_execution_service import chain_execution_service
                chain_execution_service.sync_register_webhooks_for_events(created_events, db_job.task_type)

            result = Job.from_orm(db_job)
            result.chained_events = _load_chained_events(self.db, db_job.id)
            return result
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error creating job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error creating job: {str(e)}"
            )

    def update_job(self, job_id: int, job_data: JobUpdate) -> Job:
        """
        Update an existing job
        
        Args:
            job_id: The ID of the job to update
            job_data: The job data to update
            
        Returns:
            The updated job
            
        Raises:
            HTTPException: If job not found or error updating
        """
        # Get the existing job
        db_job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        if not db_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
            
        try:
            # Update the job fields that are provided
            update_data = job_data.dict(exclude_unset=True)
            
            # Special handling for entity_ids (convert to JSON string)
            if "entity_ids" in update_data:
                update_data["entity_ids"] = json.dumps(update_data["entity_ids"]) if update_data["entity_ids"] else None
            
            # Special handling for group_ids (convert to JSON string)
            if "group_ids" in update_data:
                update_data["group_ids"] = json.dumps(update_data["group_ids"]) if update_data["group_ids"] else None
            
            # snapshot_write_method column is NOT NULL; default to UPSERT when sent as null
            if "snapshot_write_method" in update_data and update_data["snapshot_write_method"] is None:
                update_data["snapshot_write_method"] = 'UPSERT'
            
            # Validate group jobs have group_ids (check both new task_type and existing)
            final_task_type = update_data.get("task_type", db_job.task_type)
            if final_task_type in [TaskType.GROUP_START, TaskType.GROUP_STOP, TaskType.GROUP_SNAPSHOT, TaskType.GROUP_REDO]:
                final_group_ids = update_data.get("group_ids", db_job.group_ids)
                # Parse group_ids if it's a JSON string
                if isinstance(final_group_ids, str) and final_group_ids:
                    try:
                        parsed_group_ids = json.loads(final_group_ids)
                        if not parsed_group_ids or len(parsed_group_ids) == 0:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Task type {final_task_type} requires group_ids to be provided"
                            )
                    except json.JSONDecodeError:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Invalid group_ids format - must be valid JSON array"
                        )
                elif not final_group_ids:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Task type {final_task_type} requires group_ids to be provided"
                    )
            
            # Handle schedule conversion to cron_expression if schedule is provided
            if "schedule" in update_data and update_data["schedule"]:
                try:
                    # Log the received schedule for debugging
                    logger.info(f"Update includes schedule: {update_data['schedule']}")
                    
                    # Convert schedule to ScheduleConfig if it's a dict
                    schedule = update_data['schedule']
                    if isinstance(schedule, dict):
                        try:
                            schedule = ScheduleConfig(**schedule)
                            minute = schedule.minute
                            hour = schedule.hour
                            days_of_week = schedule.days_of_week
                        except Exception as e:
                            logger.error(f"Error converting schedule dict to ScheduleConfig: {e}")
                            # Ensure we have the required fields with proper defaults
                            minute = schedule.get('minute', 0) if 'minute' in schedule else 0
                            hour = schedule.get('hour', 0) if 'hour' in schedule else 0
                            days_of_week = schedule.get('days_of_week', []) if 'days_of_week' in schedule else []
                    else:
                        minute = schedule.minute
                        hour = schedule.hour
                        days_of_week = schedule.days_of_week
                    
                    # Validate required fields
                    if minute is None or hour is None:
                        raise ValueError("Schedule must include both hour and minute")
                        
                    logger.info(f"Schedule components in update: hour={hour}, minute={minute}, days={days_of_week}")
                    
                    # Convert days of week to cron format (0-6, where 0 is Sunday)
                    dow_map = {
                        "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
                        "thursday": 4, "friday": 5, "saturday": 6
                    }
                    
                    # Create reverse lookup for verification
                    reverse_dow_map = {str(v): k for k, v in dow_map.items()}
                    
                    # Log the actual day of week values for debugging
                    logger.info(f"Converting days of week: {days_of_week} to cron format")
                    
                    # Handle days of week (could be strings or DayOfWeek enums)
                    if days_of_week:
                        dow_values = []
                        for day in days_of_week:
                            # Handle both string and enum values 
                            day_str = day.lower() if isinstance(day, str) else day.value.lower()
                            if day_str in dow_map:
                                dow_values.append(str(dow_map[day_str]))
                                logger.info(f"Day {day_str} mapped to cron value {dow_map[day_str]}")
                            else:
                                logger.warning(f"Unknown day of week: {day}")
                    else:
                        dow_values = ["*"]  # All days
                        
                    dow_string = ",".join(dow_values)
                    
                    # Create cron expression (minute hour * * day_of_week)
                    cron_expression = f"{minute} {hour} * * {dow_string}"
                    update_data["cron_expression"] = cron_expression
                    
                    # Set flag to false since we're using a schedule
                    update_data["is_cron_expression"] = False
                    
                    logger.info(f"Generated cron expression from update schedule: {cron_expression}")
                    
                    # Add validation to ensure cron expression correctly represents the requested schedule
                    # This helps identify any issues with day mapping
                    for day in days_of_week:
                        day_str = day.lower() if isinstance(day, str) else day.value.lower()
                        if day_str in dow_map and str(dow_map[day_str]) not in dow_string:
                            logger.error(f"Day validation failed: {day_str} should be in cron expression but isn't")
                            
                    # Perform a reverse check to validate we can extract the correct days from the cron
                    # This validates our round-trip conversion
                    test_days = []
                    for part in dow_string.split(','):
                        if part in reverse_dow_map:
                            test_days.append(reverse_dow_map[part])
                    
                    expected_days = [day.lower() if isinstance(day, str) else day.value.lower() for day in days_of_week]
                    logger.info(f"Day validation - expected: {expected_days}, extracted: {test_days}")
                    
                    # If expected days don't match extracted days, log an error
                    missing_days = [day for day in expected_days if day not in test_days]
                    if missing_days:
                        logger.error(f"Days missing in cron expression: {missing_days}")
                        # Update dow_string to include the missing days
                        for day in missing_days:
                            if day in dow_map:
                                dow_values.append(str(dow_map[day]))
                        # Regenerate the dow_string
                        dow_string = ','.join(dow_values)
                    
                    cron_updated = True
                except Exception as e:
                    logger.error(f"Failed to convert schedule to cron expression in update: {e}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Failed to convert schedule to cron expression: {str(e)}"
                    )
            elif "cron_expression" in update_data:
                # Set flag to true since we're using a cron expression
                update_data["is_cron_expression"] = True
                cron_updated = True
            else:
                # Check if cron_expression is being updated
                cron_updated = "cron_expression" in update_data
            
            # Update the job record  (skip chained_events — handled separately below)
            for key, value in update_data.items():
                if key == "chained_events":
                    continue
                # Special handling for schedule to preserve original days
                if key == "schedule" and value is not None:
                    # Store original schedule days for validation
                    if isinstance(value, dict):
                        days_of_week = value.get('days_of_week', [])
                    else:
                        days_of_week = value.days_of_week if hasattr(value, 'days_of_week') else []
                    
                    original_days = [d.lower() if isinstance(d, str) else d.value.lower() 
                                    for d in days_of_week] if days_of_week else []
                    logger.info(f"Setting schedule with original days: {original_days}")
                
                setattr(db_job, key, value)
                
            # Always update the updated_at timestamp
            db_job.updated_at = datetime.now(timezone.utc)
            
            # Recalculate start_time if cron_expression was updated
            if cron_updated and db_job.cron_expression:
                try:
                    from croniter import croniter
                    # Get the current configured timezone from database
                    current_timezone = self._get_configured_timezone()
                    tz = pytz.timezone(current_timezone)
                    logger.info(f"Using timezone for calculation: {current_timezone}")
                    
                    now = datetime.now(tz)
                    
                    # Parse the cron expression to verify day of week values
                    cron_parts = db_job.cron_expression.split()
                    if len(cron_parts) == 5:
                        dow_part = cron_parts[4]  # 5th part is day of week
                        logger.info(f"Cron day of week part: {dow_part}")
                        
                        # Map from cron day nums (0-6) to day names for validation
                        reverse_day_map = {
                            "0": "sunday", "1": "monday", "2": "tuesday", "3": "wednesday",
                            "4": "thursday", "5": "friday", "6": "saturday"
                        }
                        
                        # Determine which days this cron will actually run on (for validation)
                        actual_days = []
                        if dow_part != "*":
                            for part in dow_part.split(','):
                                if "-" in part:
                                    start, end = part.split("-")
                                    for d in range(int(start), int(end) + 1):
                                        if str(d) in reverse_day_map:
                                            actual_days.append(reverse_day_map[str(d)])
                                elif part in reverse_day_map:
                                    actual_days.append(reverse_day_map[part])
                        
                        logger.info(f"Actual days job will run based on cron: {actual_days}")
                    
                    # Use croniter to calculate the next run time
                    cron_iter = croniter(db_job.cron_expression, now)
                    next_run_datetime = cron_iter.get_next(datetime)
                    
                    # Make sure the datetime has the correct timezone
                    if next_run_datetime.tzinfo is None:
                        next_run_datetime = tz.localize(next_run_datetime)
                    elif str(next_run_datetime.tzinfo) != str(tz):
                        # Convert to the configured timezone
                        next_run_datetime = next_run_datetime.astimezone(tz)
                    
                    # Log the day of week from the calculated next run - this helps debug timezone shift issues
                    weekday_name = next_run_datetime.strftime("%A").lower()
                    logger.info(f"Next run calculated for: {next_run_datetime}, which is a {weekday_name}")
                    
                    # Format the start_time in the required format with explicit timezone info
                    db_job.start_time = next_run_datetime.strftime("%Y-%m-%dT%H:%M:%S%z")
                    # Persist the tz-aware next run time so list views don't recompute it
                    db_job.next_run = next_run_datetime
                    # Store the timezone name as well
                    db_job.timezone_name = current_timezone
                    logger.info(f"Recalculated next run time for job {job_id}: {db_job.start_time} in timezone {db_job.timezone_name}")
                except Exception as e:
                    logger.error(f"Error recalculating next run time: {e}")
                    # If calculation fails, we won't update start_time
            
            # Update in database
            self.db.commit()
            self.db.refresh(db_job)
            
            # Update the scheduled job
            if db_job.enabled:
                # Update the job in the scheduler
                job_id = self.scheduler_service.update_job(db_job)
                
                # Use consistent command format for all jobs to ensure they're treated the same
                db_job.command = f"Scheduled job ID: {job_id}"
                self.db.commit()
                self.db.refresh(db_job)
            else:
                # Remove from scheduler if disabled
                self.scheduler_service.remove_job(db_job.id)
                
                # Ensure command is not NULL when disabled
                if db_job.command is None:
                    db_job.command = "disabled"
                    self.db.commit()
                    self.db.refresh(db_job)
            
            # Replace chained events when the caller provides a list (even empty)
            if "chained_events" in job_data.dict(exclude_unset=True):
                # Collect old event IDs for webhook cleanup
                old_events = self.db.query(ChainedJobEvent).filter(
                    ChainedJobEvent.parent_job_id == db_job.id
                ).all()
                old_event_ids = [e.id for e in old_events]

                # Delete old webhooks from CoreHub
                if old_event_ids:
                    from gluesync_scheduler.services.chain_execution_service import chain_execution_service
                    chain_execution_service.sync_delete_webhooks_for_events(old_event_ids)

                self.db.query(ChainedJobEvent).filter(
                    ChainedJobEvent.parent_job_id == db_job.id
                ).delete()
                self.db.commit()
                if job_data.chained_events:
                    for pos, ce in enumerate(job_data.chained_events):
                        chained = ChainedJobEvent(
                            parent_job_id=db_job.id,
                            position=pos,
                            task_type=ce.task_type,
                            pipeline_id=ce.pipeline_id,
                            entity_ids=json.dumps(ce.entity_ids) if ce.entity_ids else None,
                            group_ids=json.dumps(ce.group_ids) if ce.group_ids else None,
                            with_snapshot=ce.with_snapshot,
                            snapshot_write_method=ce.snapshot_write_method,
                            execution_mode=ExecutionMode(ce.execution_mode.value),
                            webhook_timeout_seconds=ce.webhook_timeout_seconds,
                        )
                        self.db.add(chained)
                    self.db.commit()

                    # Register persistent webhooks for new SYNC events
                    new_events = (
                        self.db.query(ChainedJobEvent)
                        .filter(ChainedJobEvent.parent_job_id == db_job.id)
                        .order_by(ChainedJobEvent.position)
                        .all()
                    )
                    from gluesync_scheduler.services.chain_execution_service import chain_execution_service
                    chain_execution_service.sync_register_webhooks_for_events(new_events, db_job.task_type)

            result = Job.from_orm(db_job)
            result.chained_events = _load_chained_events(self.db, db_job.id)
            return result
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating job: {str(e)}"
            )

    def toggle_job_status(self, job_id: int, enabled: bool) -> Job:
        """
        Enable or disable a job
        
        Args:
            job_id: The ID of the job to update
            enabled: True to enable, False to disable
            
        Returns:
            The updated job
            
        Raises:
            HTTPException: If job not found or error updating
        """
        job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
        
        try:
            # Update the job status
            job.enabled = enabled
            
            # If enabling, create the cron job
            if enabled:
                # Log detailed cron expression to help with debugging
                logger.info(f"Enabling job {job_id} with cron expression: {job.cron_expression}")
                if hasattr(job, 'timezone_name') and job.timezone_name:
                    logger.info(f"Job timezone: {job.timezone_name}")
                
                scheduler_job_id = self.scheduler_service.create_job(job)
                job.command = f"Scheduled job ID: {scheduler_job_id}"
                logger.info(f"Job enabled with scheduler ID: {scheduler_job_id}")
            # If disabling, delete the scheduled job
            else:
                logger.info(f"Disabling job {job_id}")
                self.scheduler_service.remove_job(job.id)
                # Ensure command is not NULL when disabling
                if not job.command:
                    job.command = "disabled"
            
            job.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(job)
            
            return Job.from_orm(job)
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating job status: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating job status: {str(e)}"
            )

    def run_job(self, job_id: int) -> dict:
        """
        Run a job immediately
        
        Args:
            job_id: The ID of the job to run
            
        Returns:
            A dictionary with execution results
            
        Raises:
            HTTPException: If job not found or error running job
        """
        # Get the job from the database
        job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
        
        # Log complete job details from database for debugging
        logger.info(f"=== JOB EXECUTION DEBUG FOR JOB {job_id} ===")
        logger.info(f"Job name: {job.name}")
        logger.info(f"Task type: {job.task_type}")
        logger.info(f"Pipeline ID: {job.pipeline_id}")
        logger.info(f"Entity IDs (raw): {repr(job.entity_ids)}")
        logger.info(f"Group IDs (raw): {repr(job.group_ids)}")
        logger.info(f"With snapshot: {job.with_snapshot}")
        logger.info(f"Snapshot write method: {getattr(job, 'snapshot_write_method', 'N/A')}")
        logger.info(f"=== END JOB DEBUG ===")
        
        # Check for group task types without group_ids
        if job.task_type in [TaskType.GROUP_START, TaskType.GROUP_STOP, TaskType.GROUP_SNAPSHOT]:
            if not job.group_ids or job.group_ids == 'null' or job.group_ids == '[]':
                error_msg = f"Job {job_id} is configured as {job.task_type} but has no group_ids. Raw value: {repr(job.group_ids)}"
                logger.error(error_msg)
                return {
                    "success": False,
                    "message": error_msg,
                    "job_id": job_id,
                    "task_type": str(job.task_type),
                    "group_ids_raw": job.group_ids
                }
        
        try:
            # Update last run time
            now = datetime.now(timezone.utc)
            job.last_run = now
            
            # Log job execution
            logger.info(f"Running job {job_id}: {job.name}")
            
            # Execute the job logic
            success, message, details = self._execute_job_logic(job)
            
            # Update job status based on execution result
            if success:
                job.last_successful_run = now
                job.last_error_message = None
                job.last_run_error_time = None
                logger.info(f"Job {job_id} executed successfully: {message}")
            else:
                job.last_error_message = message
                job.last_run_error_time = now
                logger.error(f"Job {job_id} execution failed: {message}")
            
            # Save the updated job status
            self.db.commit()

            # Fire chained events in the background (non-blocking)
            if success:
                import threading
                import asyncio as _asyncio
                from gluesync_scheduler.services.chain_execution_service import chain_execution_service
                from gluesync_scheduler.db.database import SessionLocal

                def _run_chain(parent_job_id: int):
                    chain_db = SessionLocal()
                    try:
                        parent = chain_db.query(ScheduledJob).filter(
                            ScheduledJob.id == parent_job_id
                        ).first()
                        if parent:
                            _asyncio.run(chain_execution_service.execute_chain(parent, chain_db))
                    finally:
                        chain_db.close()

                t = threading.Thread(target=_run_chain, args=(job_id,), daemon=True)
                t.start()

            # Return an extremely minimal response structure to avoid any possible recursion
            # Only use primitive types (strings, numbers, booleans)
            return {
                "success": success,
                "message": message,
                "job_id": job_id,
                "chain_started": success,
                "chain_status": "running" if success else "not_started",
                "timestamp": str(datetime.now(timezone.utc))
            }
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error running job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error running job: {str(e)}"
            )
    
    def _safe_response(self, response_text: str) -> dict:
        """
        Safely handle API response to avoid recursion issues
        
        Args:
            response_text: The response text from the API
            
        Returns:
            A simplified dictionary with only primitive types
        """
        try:
            # Try to parse as JSON
            import json
            data = json.loads(response_text)
            return {
                "status": "success",
                "message": str(data.get("message", "Operation completed"))[:200],
                "timestamp": datetime.now().isoformat()
            }
        except json.JSONDecodeError:
            return {
                "status": "success", 
                "message": str(response_text)[:200],
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)[:200],
                "timestamp": datetime.now().isoformat()
            }

    def _execute_job_logic(self, job) -> tuple:
        """
        Execute the job logic and return results
        
        Returns:
            Tuple of (success, message, details)
        """
        try:
            logger.info(f"Starting execution of job {job.id}: {job.name} (type: {job.task_type})")
            
            # Parse entity_ids if present
            entity_ids = []
            if job.entity_ids:
                try:
                    entity_ids = json.loads(job.entity_ids)
                    logger.info(f"Parsed entity_ids: {entity_ids}")
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse entity_ids JSON: {job.entity_ids}")
            
            # Parse group_ids if present
            group_ids = []
            logger.info(f"Raw job.group_ids from database: {repr(job.group_ids)} (type: {type(job.group_ids)})")
            if job.group_ids:
                try:
                    group_ids = json.loads(job.group_ids)
                    logger.info(f"Successfully parsed group_ids: {group_ids} (count: {len(group_ids)})")
                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse group_ids JSON: {job.group_ids}, error: {e}")
            else:
                logger.info("No group_ids found in job - job.group_ids is None or empty")
            
            # Use localhost for internal API calls, not the binding address (0.0.0.0)
            # Use HTTPS protocol when SSL is enabled
            ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
            protocol = "https" if ssl_enabled else "http"
            internal_host = os.getenv('SCHEDULER_INTERNAL_HOST', 'localhost')
            default_internal_port = os.getenv('SCHEDULER_INTERNAL_PORT')
            if default_internal_port is None:
                default_internal_port = os.getenv('PORT', '8000')
            internal_port = int(default_internal_port)
            base_url = f"{protocol}://{internal_host}:{internal_port}/api"
            
            # Clean log output to remove any potential hidden characters
            logger.info(f"Using internal API URL: {base_url} (SSL: {ssl_enabled})")
            
            # Determine the endpoint based on task type and set the HTTP method
            method = "POST"  # All our endpoints use POST method
            
            # Determine the action for the endpoint path
            if job.task_type in [TaskType.PIPELINE_START, TaskType.ENTITY_START, TaskType.GROUP_START]:
                action = "play"
            elif job.task_type in [TaskType.PIPELINE_STOP, TaskType.ENTITY_STOP, TaskType.GROUP_STOP]:
                action = "pause"
            elif job.task_type in [TaskType.PIPELINE_SNAPSHOT, TaskType.ENTITY_SNAPSHOT]:
                action = "one-time-snapshot"
            elif job.task_type in [TaskType.GROUP_SNAPSHOT]:
                action = "one-time-snapshot-group"
            elif job.task_type in [TaskType.PIPELINE_REDO, TaskType.ENTITY_REDO]:
                action = "redo"
            elif job.task_type == TaskType.GROUP_REDO:
                action = "redo-group"
            elif job.task_type == TaskType.PIPELINE_ENTER_MAINTENANCE:
                action = "enter-maintenance"
            elif job.task_type == TaskType.PIPELINE_EXIT_MAINTENANCE:
                action = "exit-maintenance"
            else:
                error_msg = f"Unknown task type: {job.task_type}"
                logger.error(error_msg)
                return False, error_msg, {}

            # For group-level operations we directly invoke CoreHub client, preserving legacy behavior
            if job.task_type in [TaskType.GROUP_START, TaskType.GROUP_STOP, TaskType.GROUP_SNAPSHOT, TaskType.GROUP_REDO]:
                return self._execute_group_operation(job, group_ids, action)

            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/{action}"

            # Prepare the JSON payload similar to CLI job runner
            json_data = {}

            if entity_ids:
                json_data["entity_ids"] = entity_ids

            if group_ids and job.task_type == TaskType.GROUP_REDO:
                json_data["group_ids"] = group_ids

            if job.with_snapshot and job.task_type in [
                TaskType.PIPELINE_START,
                TaskType.ENTITY_START,
                TaskType.PIPELINE_REDO,
                TaskType.ENTITY_REDO,
                TaskType.GROUP_REDO,
            ]:
                json_data["with_snapshot"] = True

            if job.snapshot_write_method and job.task_type in [
                TaskType.PIPELINE_SNAPSHOT,
                TaskType.ENTITY_SNAPSHOT,
                TaskType.PIPELINE_REDO,
                TaskType.ENTITY_REDO,
                TaskType.GROUP_REDO,
            ]:
                json_data["snapshot_write_method"] = job.snapshot_write_method

            logger.info(f"Endpoint: {method} {endpoint}")
            logger.info(f"JSON Payload: {json_data}")
            
            try:
                # Determine SSL verification settings
                # For HTTPS, we may need to skip verification if using self-signed certs
                if protocol == "https":
                    # Skip verification if SSL_SKIP_VERIFY is enabled
                    ssl_skip_verify = os.getenv('SSL_SKIP_VERIFY', 'False').lower() in ('true', '1', 't')
                    verify = not ssl_skip_verify
                    logger.info(f"Using HTTPS with SSL verification: {verify}")
                else:
                    # For HTTP, verification is not applicable
                    verify = True
                
                # Add certificate paths if available and using HTTPS
                cert = None
                if protocol == "https":
                    cert_file = os.environ.get('SSL_CERT_FILE')
                    key_file = os.environ.get('SSL_KEY_FILE')
                    if cert_file and os.path.exists(cert_file) and key_file and os.path.exists(key_file):
                        cert = (cert_file, key_file)
                        logger.info(f"Using certificate files for HTTPS request: {cert_file} and {key_file}")
                
                # Make the HTTP request.
                # Timeout is configurable via SCHEDULER_INTERNAL_HTTP_TIMEOUT (seconds).
                # The previous hard-coded 30s caused false "Read timed out" failures when
                # multiple cron jobs on the same pipeline collided (e.g. at :00 and :30).
                internal_http_timeout = int(os.getenv('SCHEDULER_INTERNAL_HTTP_TIMEOUT', '120'))
                response = requests.request(
                    method=method,
                    url=endpoint,
                    json=json_data,
                    headers={"Content-Type": "application/json"},
                    timeout=internal_http_timeout,
                    verify=verify,  # Control SSL certificate verification
                    cert=cert  # Include certificates for client authentication if available
                )
                logger.info(f"Response status code: {response.status_code}")
                logger.info(f"Response headers: {response.headers}")
                
                # Log a limited preview of the response for debugging
                # This prevents large responses from flooding the logs
                response_preview = response.text[:100] + '...' if len(response.text) > 100 else response.text
                logger.info(f"Response preview: {response_preview}")
            except requests.exceptions.RequestException as e:
                logger.error(f"HTTP request failed: {str(e)}")
                return False, f"HTTP request failed: {str(e)}", {}
            
            # Check the response
            if response.status_code in [200, 201, 202]:
                # Don't try to parse the response JSON, just return a simple success message
                # This completely avoids any potential recursion issues
                success_msg = f"Job executed successfully with status code {response.status_code}"
                logger.info(success_msg)
                
                # Create a new, completely flat response with only primitive types
                # This completely eliminates the possibility of recursion errors
                result = {
                    "status": "success",
                    "code": str(response.status_code),
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                }
                
                # We don't even include the response preview in the result
                # Just log it and return a simple success flag
                
                return True, success_msg, result
            else:
                # For error responses, just log the status code and a truncated response
                truncated_response = response.text[:100] + '...' if len(response.text) > 100 else response.text
                error_msg = f"Job execution failed with status {response.status_code}"
                logger.error(f"{error_msg}: {truncated_response}")
                
                # Return a minimal response with just primitive types
                # Avoid including any complex objects that might cause recursion
                result = {
                    "status_code": response.status_code,
                    "success": False,
                    "error": truncated_response,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                
                return False, error_msg, result
                
        except Exception as e:
            error_msg = f"Error executing job: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, {"exception": str(e)}
    
    def _execute_group_operation(self, job: ScheduledJob, group_ids: list, action: str) -> tuple[bool, str, dict]:
        """Execute group operations using CoreHub client directly"""
        from ..core.play_pause import CoreHubClient
        
        try:
            # Get CoreHub client instance
            corehub_client = CoreHubClient()
            
            # Determine snapshot write method from job parameters
            snapshot_write_method = getattr(job, 'snapshot_write_method', 'UPSERT')
            
            # Log and validate group_ids
            logger.info(f"Group operation requested: action={action}, pipeline_id={job.pipeline_id}")
            logger.info(f"Received group_ids parameter: {repr(group_ids)} (type: {type(group_ids)}, length: {len(group_ids) if group_ids else 'N/A'})")
            logger.info(f"Job database group_ids field: {repr(job.group_ids)} (type: {type(job.group_ids)})")
            
            if not group_ids:
                warn_msg = f"No group_ids provided for group {action} operation; aborting. Check if job was created with group_ids."
                logger.warning(warn_msg)
                details = {
                    "total_groups": 0,
                    "successful_groups": 0,
                    "failed_groups": 0,
                    "results": [],
                    "action": action,
                    "pipeline_id": job.pipeline_id,
                    "timestamp": datetime.now().isoformat()
                }
                return False, warn_msg, details
            
            success_count = 0
            total_groups = len(group_ids)
            results = []
            
            # Execute operation for each group (CoreHub only supports one group per call)
            for group_id in group_ids:
                try:
                    if action == "play":
                        # For start operations, check if with_snapshot is enabled
                        with_snapshot = getattr(job, 'with_snapshot', False)
                        result = corehub_client.start_group(
                            job.pipeline_id, 
                            group_id, 
                            with_snapshot=with_snapshot,
                            snapshot_write_method=snapshot_write_method
                        )
                    elif action == "pause":
                        result = corehub_client.stop_group(job.pipeline_id, group_id)
                    elif action == "resync" or action == "one-time-snapshot-group":
                        result = corehub_client.resync_group(
                            job.pipeline_id,
                            group_id,
                            snapshot_write_method=snapshot_write_method
                        )
                    elif action == "redo-group":
                        with_snapshot = getattr(job, 'with_snapshot', False)
                        result = corehub_client.redo_group(
                            job.pipeline_id,
                            group_id,
                            with_snapshot=with_snapshot,
                            snapshot_write_method=snapshot_write_method
                        )
                    else:
                        logger.error(f"Unknown action for group operation: {action}")
                        result = False
                    
                    if result:
                        success_count += 1
                        results.append({"group_id": group_id, "status": "success"})
                        logger.info(f"Successfully executed {action} for group {group_id}")
                    else:
                        results.append({"group_id": group_id, "status": "failed"})
                        logger.error(f"Failed to execute {action} for group {group_id}")
                        
                except Exception as e:
                    results.append({"group_id": group_id, "status": "error", "error": str(e)})
                    logger.error(f"Error executing {action} for group {group_id}: {str(e)}")
            
            # Determine overall success
            overall_success = success_count == total_groups
            
            if overall_success:
                message = f"Successfully executed {action} for all {total_groups} groups"
            else:
                message = f"Executed {action} for {success_count}/{total_groups} groups successfully"
            
            details = {
                "total_groups": total_groups,
                "successful_groups": success_count,
                "failed_groups": total_groups - success_count,
                "results": results,
                "action": action,
                "pipeline_id": job.pipeline_id,
                "timestamp": datetime.now().isoformat()
            }
            
            return overall_success, message, details
            
        except Exception as e:
            error_msg = f"Error executing group {action} operation: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, {"error": str(e), "timestamp": datetime.now().isoformat()}

    def delete_job(self, job_id: int) -> None:
        """
        Delete a job
        
        Args:
            job_id: The ID of the job to delete
            
        Raises:
            HTTPException: If job not found or error deleting
        """
        # Get the existing job
        db_job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        if not db_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
        
        try:
            # Delete persistent webhooks from CoreHub before DB cascade
            from gluesync_scheduler.services.chain_execution_service import chain_execution_service
            chain_execution_service.sync_delete_webhooks_for_job(db_job.id)

            # Delete the scheduled job first
            self.scheduler_service.remove_job(db_job.id)
            
            # Then delete from database (chained events cascade-delete via FK)
            self.db.delete(db_job)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error deleting job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error deleting job: {str(e)}"
            )


# ---------------------------------------------------------------------------
# Module-level helper (not a method — avoids duplication across get_* methods)
# ---------------------------------------------------------------------------

def _rows_to_chained_responses(rows: list) -> list:
    """Convert ChainedJobEvent ORM rows to ChainedEventResponse objects."""
    result = []
    for row in rows:
        resp = ChainedEventResponse(
            id=row.id,
            position=row.position,
            parent_job_id=row.parent_job_id,
            task_type=row.task_type,
            pipeline_id=row.pipeline_id,
            entity_ids=json.loads(row.entity_ids) if row.entity_ids else None,
            group_ids=json.loads(row.group_ids) if row.group_ids else None,
            with_snapshot=row.with_snapshot,
            snapshot_write_method=row.snapshot_write_method,
            execution_mode=row.execution_mode.value,
        )
        result.append(resp)
    return result


def _load_chained_events(db, parent_job_id: int) -> list:
    """Query and return ChainedEventResponse objects for a given job."""
    rows = (
        db.query(ChainedJobEvent)
        .filter(ChainedJobEvent.parent_job_id == parent_job_id)
        .order_by(ChainedJobEvent.position)
        .all()
    )
    return _rows_to_chained_responses(rows)
