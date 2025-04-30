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

import logging
import os
import requests
from datetime import datetime, timezone
import json
from typing import Dict, Optional, List, Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from fastapi import HTTPException, status

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.models.models import ScheduledJob

logger = logging.getLogger(__name__)

class SchedulerService:
    """Service for managing scheduled jobs using APScheduler"""
    
    _instance = None
    
    @classmethod
    def get_instance(cls):
        """Get the singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        """Initialize the scheduler service"""
        if SchedulerService._instance is not None:
            raise Exception("This class is a singleton. Use get_instance() instead.")
        
        try:
            # Create log directory if it doesn't exist
            os.makedirs(settings.CRON_LOG_DIR, exist_ok=True)
            
            # Initialize the scheduler
            self.scheduler = BackgroundScheduler(
                jobstores={
                    'default': MemoryJobStore()
                },
                timezone=settings.TIMEZONE
            )
            
            # Start the scheduler
            self.scheduler.start()
            logger.info("APScheduler initialized and started")
            
        except Exception as e:
            logger.error(f"Error initializing scheduler: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error initializing scheduler: {str(e)}"
            )
    
    def create_job(self, job: ScheduledJob) -> str:
        """
        Create a new scheduled job
        
        Args:
            job: The scheduled job to create
            
        Returns:
            The job ID
            
        Raises:
            HTTPException: If there's an error creating the job
        """
        try:
            # Parse the cron expression
            cron_parts = job.cron_expression.split()
            if len(cron_parts) != 5:
                raise ValueError(f"Invalid cron expression: {job.cron_expression}")
            
            minute, hour, day, month, day_of_week = cron_parts
            
            # Log the cron component values for debugging
            logger.info(f"Cron components - minute: {minute}, hour: {hour}, day: {day}, month: {month}, day_of_week: {day_of_week}")
            
            # Create the job in the scheduler
            job_id = f"job_{job.id}"
            
            # Get the job's timezone or fall back to the global timezone setting
            job_timezone = job.timezone_name if hasattr(job, 'timezone_name') and job.timezone_name else settings.TIMEZONE
            logger.info(f"Using timezone {job_timezone} for job {job_id}")
            
            # Map from day numbers (0-6) to day names for validation/logging
            day_map = {
                "0": "sunday", "1": "monday", "2": "tuesday", "3": "wednesday",
                "4": "thursday", "5": "friday", "6": "saturday"
            }
            
            # Log the actual days of week this job will run on (for validation)
            if day_of_week != "*":
                day_names = []
                for part in day_of_week.split(','):
                    if "-" in part:
                        start, end = part.split("-")
                        for d in range(int(start), int(end) + 1):
                            if str(d) in day_map:
                                day_names.append(day_map[str(d)])
                    elif part in day_map:
                        day_names.append(day_map[part])
                logger.info(f"Job will run on these days: {day_names}")
                # Use day NAMES for APScheduler instead of numbers to avoid confusion
                # APScheduler uses a different numbering system (0=Monday) than standard cron (0=Sunday)
                cron_dow = ",".join([day.upper() for day in day_names])  # Convert to uppercase day names for APScheduler
            else:
                logger.info("Job will run every day")
                cron_dow = "*"
            
            # Create the trigger with the job's timezone
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=cron_dow,
                timezone=job_timezone
            )
            
            # Calculate the next run time based on the trigger
            from datetime import datetime
            import pytz
            
            # Get the next run time from the trigger (for validation)
            tz = pytz.timezone(job_timezone)
            now = datetime.now(tz)  # Get current time in job's timezone
            
            # Create the trigger with explicit timezone
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=cron_dow,
                timezone=tz
            )
            
            # Get next run time and ensure it's in the correct timezone
            next_run = trigger.get_next_fire_time(None, now)
            
            if next_run:
                # Ensure next_run is in the job's timezone
                if next_run.tzinfo != tz:
                    next_run = next_run.astimezone(tz)
                    
                # Log the calculated next run time and day for validation
                weekday_name = next_run.strftime("%A").lower()
                weekday_number = next_run.weekday() # 0 is Monday, 6 is Sunday
                # Convert to cron weekday where 0 is Sunday, 6 is Saturday
                cron_weekday = (weekday_number + 1) % 7
                logger.info(f"Next scheduled run will be: {next_run.strftime('%Y-%m-%d %H:%M:%S %z')} which is a {weekday_name}")
                logger.info(f"Weekday validation - Cron format: {cron_dow}, Actual date weekday: {cron_weekday}")
                
                # Verify if the scheduled date actually matches the expected day of week
                expected_days = cron_dow.split(',')
                actual_day = weekday_name.upper() # Convert to uppercase for comparison
                if cron_dow != '*' and actual_day not in expected_days:
                    logger.warning(f"WARNING: Next run date {next_run.strftime('%Y-%m-%d')} is a {weekday_name.upper()}, but job is configured to run on weekdays: {cron_dow}. This may indicate a timezone issue.")
                
                # Now with day names, the comparison is much simpler
                # Just check if the uppercase day name from the next run time
                # matches any of our expected days
                weekday_name_upper = weekday_name.upper()
                
                # Only perform check if we have specific days (not *)
                # and if we haven't already shown a warning
                if cron_dow != '*' and actual_day not in expected_days:
                    logger.warning(f"Warning: Next run time {next_run} is scheduled for {weekday_name_upper}, but job is configured to run on: {cron_dow}")
                    logger.warning(f"This may indicate a timezone alignment issue or that the next valid run time is in a future week")
            else:
                logger.warning(f"Could not determine next run time for job {job_id} with cron expression {job.cron_expression}")
            
            # Add the job to the scheduler with proper next run time calculation
            # We want it to run on schedule but not immediately
            self.scheduler.add_job(
                self._execute_job,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                args=[job.id, job.name],
                misfire_grace_time=3600,   # Allow misfires up to an hour
                coalesce=True              # Only run once if multiple executions are missed
                # Let the trigger naturally determine the next run time
            )
            
            logger.info(f"Created scheduled job: {job_id} with expression: {job.cron_expression}")
            return job_id
            
        except Exception as e:
            logger.error(f"Error creating scheduled job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error creating scheduled job: {str(e)}"
            )
    
    def update_job(self, job: ScheduledJob) -> str:
        """
        Update an existing scheduled job
        
        Args:
            job: The scheduled job to update
            
        Returns:
            The job ID
            
        Raises:
            HTTPException: If there's an error updating the job
        """
        try:
            # Log more details about the job we're updating for debugging
            logger.info(f"Updating scheduled job for job_id={job.id} with cron_expression={job.cron_expression}")
            if hasattr(job, 'timezone_name') and job.timezone_name:
                logger.info(f"Job timezone: {job.timezone_name}")
            
            # Remove the existing job
            self.remove_job(job.id)
            
            # Create a new job with the updated settings
            job_id = self.create_job(job)
            logger.info(f"Job updated successfully with scheduler ID: {job_id}")
            return job_id
            
        except Exception as e:
            logger.error(f"Error updating scheduled job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating scheduled job: {str(e)}"
            )
    
    def remove_job(self, job_id: int) -> None:
        """
        Remove a scheduled job
        
        Args:
            job_id: The ID of the job to remove
            
        Raises:
            HTTPException: If there's an error removing the job
        """
        try:
            # Remove the job from the scheduler
            scheduler_job_id = f"job_{job_id}"
            
            # Check if the job exists
            if self.scheduler.get_job(scheduler_job_id):
                self.scheduler.remove_job(scheduler_job_id)
                logger.info(f"Removed scheduled job: {scheduler_job_id}")
            
        except Exception as e:
            logger.error(f"Error removing scheduled job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error removing scheduled job: {str(e)}"
            )
    
    def _execute_job(self, job_id: int, job_name: str) -> None:
        """
        Execute a job
        
        Args:
            job_id: The ID of the job to execute
            job_name: The name of the job (for logging)
        """
        try:
            log_file = os.path.join(settings.CRON_LOG_DIR, f"job_{job_id}.log")
            error_file = os.path.join(settings.CRON_LOG_DIR, f"job_{job_id}.error.log")
            
            # Log the job execution
            with open(log_file, 'a') as f:
                f.write(f"[{datetime.now(timezone.utc)}] Running job {job_id}: {job_name}\n")
            
            # DIRECTLY execute the job logic instead of making an HTTP request
            # This avoids any potential recursion issues with JSON serialization
            logger.info(f"Executing scheduled job {job_id}: {job_name}")
            
            # Import here to avoid circular imports
            from gluesync_scheduler.db.database import get_db
            from gluesync_scheduler.services.job_service import JobService
            
            # Get a database connection
            db = next(get_db())
            
            try:
                # Create job service and run the job directly
                job_service = JobService(db)
                result = job_service.run_job(job_id)
                
                # Extract values from the result dictionary
                success = result.get('success', False)
                message = result.get('message', 'Unknown result')
                details = result  # Use the whole result dict as details
                
                # Log the results without serializing the entire response
                with open(log_file, 'a') as f:
                    f.write(f"[{datetime.now(timezone.utc)}] Job execution {'succeeded' if success else 'failed'}\n")
                    f.write(f"Message: {message}\n")
                    
                    # Only log a limited set of details to avoid recursion issues
                    if isinstance(details, dict):
                        safe_details = {
                            k: str(v)[:100] if not isinstance(v, (int, float, bool)) else v 
                            for k, v in details.items()
                        }
                        f.write(f"Details: {safe_details}\n")
                
                # Log success or failure
                if success:
                    logger.info(f"Successfully executed job {job_id}: {message}")
                else:
                    error_message = f"Error in job {job_id}: {message}"
                    with open(error_file, 'a') as f:
                        f.write(f"[{datetime.now(timezone.utc)}] {error_message}\n")
                    logger.error(error_message)
            
            except Exception as e:
                error_message = f"Exception occurred while executing job {job_id}: {str(e)}"
                logger.error(error_message)
                
                # Log to error file
                with open(error_file, 'a') as f:
                    f.write(f"[{datetime.now()}] {error_message}\n")
            
        except Exception as e:
            logger.error(f"Error executing job {job_id}: {str(e)}")
            
            # Log the error to the error file
            try:
                with open(error_file, 'a') as f:
                    f.write(f"[{datetime.now(timezone.utc)}] Error executing job {job_id}: {str(e)}\n")
            except Exception:
                pass

# Create a global instance for easy import
scheduler_service = SchedulerService.get_instance()
