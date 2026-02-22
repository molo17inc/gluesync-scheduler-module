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
import sys
from typing import Optional
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from croniter import croniter
from sqlalchemy.orm import Session

from gluesync_scheduler.models.models import ScheduledJob
from gluesync_scheduler.core.timezone_utils import get_env_timezone

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
            cron_log_dir = os.getenv('CRON_LOG_DIR', './logs')
            os.makedirs(cron_log_dir, exist_ok=True)
            
            # Initialize the scheduler
            self.scheduler = AsyncIOScheduler(
                jobstores={
                    'default': MemoryJobStore()
                },
                job_defaults={
                    'coalesce': True,
                    'max_instances': 1,
                    'misfire_grace_time': 3600,
                },
                timezone=get_env_timezone('UTC')
            )
            
            # Start the scheduler
            self.scheduler.start()
            logger.info("APScheduler initialized and started")
            
        except Exception as e:
            logger.error(f"Error initializing scheduler: {str(e)}")
            raise RuntimeError(f"Error initializing scheduler: {str(e)}")
    
    def create_job(self, job: ScheduledJob) -> str:
        """
        Create a new scheduled job
        
        Args:
            job: The scheduled job to create
            
        Returns:
            The job ID
            
        Raises:
            RuntimeError: If there's an error creating the job
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
            job_timezone = job.timezone_name if hasattr(job, 'timezone_name') and job.timezone_name else get_env_timezone('UTC')
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
                # Convert from standard cron days (where 0=Sunday) to APScheduler day names
                # because APScheduler has inconsistent handling of numeric weekdays
                cron_day_to_name = {
                    "0": "sun", "1": "mon", "2": "tue", "3": "wed", 
                    "4": "thu", "5": "fri", "6": "sat"
                }
                
                # Convert each day number to its name
                dow_parts = day_of_week.split(',')
                dow_names = []
                for part in dow_parts:
                    if part in cron_day_to_name:
                        dow_names.append(cron_day_to_name[part])
                    else:
                        # Handle ranges or unknown parts by passing them through
                        dow_names.append(part)
                
                # Use APScheduler's expected day names
                cron_dow = ','.join(dow_names)  # e.g., "wed" instead of "3"
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
                # Convert the Python weekday to the same format we're using with APScheduler (day names)
                python_weekday = next_run.weekday()  # 0=Monday, 1=Tuesday, ..., 6=Sunday
                
                # Map Python's weekday to day abbreviation
                python_to_apscheduler = {
                    0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"
                }
                actual_day = python_to_apscheduler[python_weekday]
                if cron_dow != '*' and actual_day not in expected_days:
                    logger.warning(f"WARNING: Next run date {next_run.strftime('%Y-%m-%d')} is a {weekday_name} ({actual_day}), but job is configured to run on weekdays: {cron_dow}. This may indicate a timezone issue or that the next valid run time is in a future week.")
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
                coalesce=True,             # Only run once if multiple executions are missed
                max_instances=1            # Avoid overlapping executions of the same job
                # Let the trigger naturally determine the next run time
            )
            
            logger.info(f"Created scheduled job: {job_id} with expression: {job.cron_expression}")
            return job_id
            
        except Exception as e:
            logger.error(f"Error creating scheduled job: {str(e)}")
            raise RuntimeError(f"Error creating scheduled job: {str(e)}")
    
    def update_job(self, job: ScheduledJob) -> str:
        """
        Update an existing scheduled job
        
        Args:
            job: The scheduled job to update
            
        Returns:
            The job ID
            
        Raises:
            RuntimeError: If there's an error updating the job
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
            raise RuntimeError(f"Error updating scheduled job: {str(e)}")
    
    def remove_job(self, job_id: int) -> None:
        """
        Remove a scheduled job
        
        Args:
            job_id: The ID of the job to remove
            
        Raises:
            RuntimeError: If there's an error removing the job
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
            raise RuntimeError(f"Error removing scheduled job: {str(e)}")
    
    def _execute_job(self, job_id: int, job_name: str) -> None:
        """
        Execute a job
        
        Args:
            job_id: The ID of the job to execute
            job_name: The name of the job (for logging)
        """
        try:
            cron_log_dir = os.getenv('CRON_LOG_DIR', './logs')
            log_file = os.path.join(cron_log_dir, f"job_{job_id}.log")
            error_file = os.path.join(cron_log_dir, f"job_{job_id}.error.log")
            
            # Log the job execution
            with open(log_file, 'a') as f:
                f.write(f"[{datetime.now(timezone.utc)}] Running job {job_id}: {job_name}\n")
            
            # DIRECTLY execute the job logic instead of making an HTTP request
            # This avoids any potential recursion issues with JSON serialization
            logger.info(f"Executing scheduled job {job_id}: {job_name}")
            
            # Import here to avoid circular imports
            from gluesync_scheduler.db.database import get_db
            from gluesync_scheduler.services.job_service import JobService
            from gluesync_scheduler.models.models import ScheduledJob
            
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
                    
                    # If FIRE_ONCE is enabled, disable the job after successful execution
                    fire_once = os.getenv('FIRE_ONCE', 'False').lower() in ('true', '1', 't')
                    if fire_once:
                        logger.info(f"FIRE_ONCE is enabled. Disabling job {job_id} after first execution")
                        
                        # Get the job from database
                        job = db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
                        if job:
                            # Disable the job
                            job.enabled = False
                            job.updated_at = datetime.now(timezone.utc)
                            db.commit()
                            
                            # Remove the job from the scheduler
                            scheduler_job_id = f"job_{job_id}"
                            if self.scheduler.get_job(scheduler_job_id):
                                self.scheduler.remove_job(scheduler_job_id)
                                logger.info(f"Removed job {scheduler_job_id} from scheduler due to FIRE_ONCE setting")
                            
                            # Log the disabling
                            with open(log_file, 'a') as f:
                                f.write(f"[{datetime.now(timezone.utc)}] Job disabled due to FIRE_ONCE setting\n")
                else:
                    error_message = f"Error in job {job_id}: {message}"
                    with open(error_file, 'a') as f:
                        f.write(f"[{datetime.now(timezone.utc)}] {error_message}\n")
                    logger.error(error_message)
                    
                    # Check if we should fail silently or raise exception
                    # If SDK connection issues, we should continue instead of crashing
                    if "Failed to connect" in message or "WebSocket" in message or "SDK" in message:
                        logger.warning(f"Job {job_id} failed due to connection issue - continuing scheduler operation")
                        # Don't raise exception for connection issues, just log
                    else:
                        # For other errors, still propagate as exception so APScheduler marks job as failed
                        # But limit how many times we retry before giving up
                        job = db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
                        if job and job.last_error_message:
                            # Check if this is a recurring error
                            if job.last_run_error_time:
                                last_error_time = job.last_run_error_time
                                if last_error_time.tzinfo is None:
                                    last_error_time = last_error_time.replace(tzinfo=timezone.utc)

                                current_time = datetime.now(timezone.utc)
                                time_since_last_error = current_time - last_error_time
                                if time_since_last_error < timedelta(hours=1):
                                    # If errors are happening frequently, log but don't raise to prevent scheduler crash
                                    logger.warning(f"Job {job_id} experiencing recurring errors - skipping exception raise to prevent scheduler crash")
                                    return
                        
                        # Otherwise, raise the exception
                        raise RuntimeError(error_message)
            
            except Exception as e:
                error_message = f"Exception occurred while executing job {job_id}: {str(e)}"
                logger.exception(error_message)
                
                # Log to error file
                with open(error_file, 'a') as f:
                    f.write(f"[{datetime.now(timezone.utc)}] {error_message}\n")
                
                # Re-raise so the scheduler can record the failure
                raise
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            
        except Exception as e:
            logger.exception(f"Error executing job {job_id}: {str(e)}")
            
            # Log the error to the error file
            try:
                with open(error_file, 'a') as f:
                    f.write(f"[{datetime.now(timezone.utc)}] Error executing job {job_id}: {str(e)}\n")
            except Exception:
                pass
            
            # Re-raise so APScheduler marks the job as failed
            raise

# Create a global instance for easy import
scheduler_service = SchedulerService.get_instance()
