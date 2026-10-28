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

import asyncio
import logging
import os
import sys
from typing import Iterable, List, NamedTuple, Optional
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from croniter import croniter
from sqlalchemy.orm import Session

from gluesync_scheduler.models.models import ScheduledJob, TaskType
from gluesync_scheduler.core.timezone_utils import get_env_timezone

logger = logging.getLogger(__name__)

_WILDCARD = "*"

# How late a fire time may still run: APScheduler's misfire grace, and the window in which
# a run missed while Chronos was down is replayed at startup
MISFIRE_GRACE_SECONDS = 3600

# Upper bound on fire times stepped through when looking for a job's latest missed fire
_MAX_CATCHUP_SCAN = 5000


def _day_or_applies(job: ScheduledJob) -> bool:
    """True when day-of-month and day-of-week must be OR-ed (standard cron) for this job."""
    if getattr(job, 'day_or', None) is False:
        return False
    try:
        expanded = croniter(job.cron_expression).expanded
    except ValueError:
        return False
    return expanded[2][0] != _WILDCARD and expanded[4][0] != _WILDCARD


def _build_trigger(use_or, minute, hour, day, month, day_of_week, tz):
    """Build the APScheduler trigger: a CronTrigger (AND) or two OR-ed CronTriggers on the day fields."""
    common = {'minute': minute, 'hour': hour, 'month': month, 'timezone': tz}
    if not use_or:
        return CronTrigger(day=day, day_of_week=day_of_week, **common)
    return OrTrigger([
        CronTrigger(day=day, day_of_week=_WILDCARD, **common),
        CronTrigger(day=_WILDCARD, day_of_week=day_of_week, **common),
    ])


# Standard cron weekday numbers (0=Sunday) to day names, for validation/logging
_CRON_DAY_NAMES = {
    "0": "sunday", "1": "monday", "2": "tuesday", "3": "wednesday",
    "4": "thursday", "5": "friday", "6": "saturday",
}

# Standard cron weekday numbers (0=Sunday) to the names APScheduler expects,
# because APScheduler has inconsistent handling of numeric weekdays
_CRON_DAY_TO_APSCHEDULER = {
    "0": "sun", "1": "mon", "2": "tue", "3": "wed",
    "4": "thu", "5": "fri", "6": "sat",
}

# Python weekday() (0=Monday) to the APScheduler day abbreviation
_PYTHON_WEEKDAY_TO_APSCHEDULER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _job_timezone_name(job: ScheduledJob) -> str:
    """The job's timezone, falling back to the global timezone setting."""
    return getattr(job, 'timezone_name', None) or get_env_timezone('UTC')


def _log_run_days(day_of_week: str) -> None:
    """Log the actual days of week a job will run on (for validation)."""
    if day_of_week == _WILDCARD:
        logger.info("Job will run every day")
        return
    day_names = []
    for part in day_of_week.split(','):
        if "-" in part:
            start, end = part.split("-")
            day_names.extend(
                _CRON_DAY_NAMES[str(d)] for d in range(int(start), int(end) + 1) if str(d) in _CRON_DAY_NAMES
            )
        elif part in _CRON_DAY_NAMES:
            day_names.append(_CRON_DAY_NAMES[part])
    logger.info(f"Job will run on these days: {day_names}")


def _to_apscheduler_day_of_week(day_of_week: str) -> str:
    """Convert standard cron day numbers to APScheduler day names (ranges/unknown parts pass through)."""
    if day_of_week == _WILDCARD:
        return _WILDCARD
    return ','.join(_CRON_DAY_TO_APSCHEDULER.get(part, part) for part in day_of_week.split(','))


def _log_next_run(next_run: datetime, cron_dow: str, use_or: bool, tz) -> None:
    """Log the calculated next run and warn when its weekday does not match the schedule."""
    if next_run.tzinfo != tz:
        next_run = next_run.astimezone(tz)

    weekday_name = next_run.strftime("%A").lower()
    # Convert to cron weekday where 0 is Sunday, 6 is Saturday
    cron_weekday = (next_run.weekday() + 1) % 7
    logger.info(f"Next scheduled run will be: {next_run.strftime('%Y-%m-%d %H:%M:%S %z')} which is a {weekday_name}")
    logger.info(f"Weekday validation - Cron format: {cron_dow}, Actual date weekday: {cron_weekday}")

    actual_day = _PYTHON_WEEKDAY_TO_APSCHEDULER[next_run.weekday()]
    if not use_or and cron_dow != _WILDCARD and actual_day not in cron_dow.split(','):
        logger.warning(f"WARNING: Next run date {next_run.strftime('%Y-%m-%d')} is a {weekday_name} ({actual_day}), but job is configured to run on weekdays: {cron_dow}. This may indicate a timezone issue or that the next valid run time is in a future week.")


def _validate_next_run(trigger, job_id: str, cron_expression: str, cron_dow: str, use_or: bool, tz) -> None:
    """Compute the trigger's next fire time (for validation) and log it."""
    next_run = trigger.get_next_fire_time(None, datetime.now(tz))
    if next_run:
        _log_next_run(next_run, cron_dow, use_or, tz)
    else:
        logger.warning(f"Could not determine next run time for job {job_id} with cron expression {cron_expression}")


def _trigger_for_job(job: ScheduledJob):
    """Build the job's trigger; returns (trigger, apscheduler day_of_week, day-OR flag, timezone)."""
    cron_parts = job.cron_expression.split()
    if len(cron_parts) != 5:
        raise ValueError(f"Invalid cron expression: {job.cron_expression}")
    minute, hour, day, month, day_of_week = cron_parts
    cron_dow = _to_apscheduler_day_of_week(day_of_week)
    tz = ZoneInfo(_job_timezone_name(job))
    use_or = _day_or_applies(job)
    return _build_trigger(use_or, minute, hour, day, month, cron_dow, tz), cron_dow, use_or, tz


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Aware datetime for a DB value: naive datetimes (SQLite) are UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _scan_fires(trigger, start: datetime, now: datetime) -> Optional[datetime]:
    """Latest fire time of the trigger in [start, now] (None if there is none), stepping through them."""
    latest = None
    previous = start - timedelta(microseconds=1)
    for _ in range(_MAX_CATCHUP_SCAN):
        fire = trigger.get_next_fire_time(previous, now)
        if fire is None or fire > now:
            break
        latest = previous = fire
    return latest


def latest_missed_fire(trigger, next_run: Optional[datetime], last_run: Optional[datetime],
                       now: datetime) -> Optional[datetime]:
    """
    The latest fire time of the trigger that was due (next_run <= now) and never ran (after last_run).

    The persisted next_run is the evidence that a fire was due; several missed fires coalesce
    into the latest one. Returns None when nothing was missed.
    """
    next_run, last_run, now = _as_utc(next_run), _as_utc(last_run), _as_utc(now)
    if next_run is None or next_run > now:
        return None

    # Only fires within the grace window are replayed, except for maintenance exit (see
    # plan_missed_runs): when next_run is older, start there (cheap for jobs that fire every
    # minute) and fall back to the full scan for the old miss
    recent_start = now - timedelta(seconds=MISFIRE_GRACE_SECONDS)
    missed = None
    if next_run < recent_start:
        missed = _scan_fires(trigger, recent_start, now)
    if missed is None:
        missed = _scan_fires(trigger, next_run, now)

    if missed is None or (last_run is not None and missed <= last_run):
        return None
    return missed


class MissedRun(NamedTuple):
    """A scheduled fire time that passed while Chronos was down and is to be replayed."""
    fire_time: datetime
    job_id: int
    job_name: str


def plan_missed_runs(jobs: Iterable[ScheduledJob], now: Optional[datetime] = None) -> List[MissedRun]:
    """
    The runs to replay at startup: for each job its latest missed fire time when it is within
    MISFIRE_GRACE_SECONDS, ordered by fire time across jobs. Older misses are only logged,
    except for pipeline_exit_maintenance jobs, which are replayed whatever the age.
    """
    now = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    plan: List[MissedRun] = []
    for job in jobs:
        try:
            trigger = _trigger_for_job(job)[0]
            missed = latest_missed_fire(trigger, job.next_run, job.last_run, now)
            if missed is None:
                continue
            age = (now - missed).total_seconds()
            if age > MISFIRE_GRACE_SECONDS:
                # A skipped exit leaves the pipeline in maintenance indefinitely, while running
                # it late only restores the normal state
                if getattr(job, 'task_type', None) != TaskType.PIPELINE_EXIT_MAINTENANCE:
                    logger.warning(
                        f"Skipping missed run of job {job.id} '{job.name}' due at {missed.isoformat()}: "
                        f"{int(age)}s ago, beyond the {MISFIRE_GRACE_SECONDS}s catch-up window")
                    continue
                logger.warning(
                    f"Job {job.id} '{job.name}' (exit maintenance) missed its run due at {missed.isoformat()}, "
                    f"{int(age)}s ago: the {MISFIRE_GRACE_SECONDS}s catch-up window is waived")
            logger.info(f"Job {job.id} '{job.name}' missed its run due at {missed.isoformat()}: will be replayed")
            plan.append(MissedRun(missed, job.id, job.name))
        except Exception as e:
            logger.exception(f"Could not work out the missed run of job {job.id} '{job.name}': {e}")
    plan.sort(key=lambda run: run.fire_time)
    return plan


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
                    'misfire_grace_time': MISFIRE_GRACE_SECONDS,
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

            _log_run_days(day_of_week)
            trigger, cron_dow, use_or, tz = _trigger_for_job(job)
            logger.info(f"Using timezone {tz.key} for job {job_id}")
            
            _validate_next_run(trigger, job_id, job.cron_expression, cron_dow, use_or, tz)
            self._add_job_to_scheduler(job, job_id, trigger)
            
            logger.info(f"Created scheduled job: {job_id} with expression: {job.cron_expression}")
            return job_id
            
        except Exception as e:
            logger.exception(f"Error creating scheduled job: {str(e)}")
            raise RuntimeError(f"Error creating scheduled job: {str(e)}") from e

    def _add_job_to_scheduler(self, job: ScheduledJob, job_id: str, trigger) -> None:
        """Add the job to the scheduler; the trigger naturally determines the next run time."""
        self.scheduler.add_job(
            self._execute_job,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            args=[job.id, job.name],
            misfire_grace_time=MISFIRE_GRACE_SECONDS,   # Allow misfires up to an hour
            coalesce=True,             # Only run once if multiple executions are missed
            max_instances=1            # Avoid overlapping executions of the same job
        )
    
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
    
    async def run_missed(self, plan: List[MissedRun]) -> None:
        """Replay the missed runs one after the other, oldest first; a failing run does not stop the rest."""
        loop = asyncio.get_running_loop()
        for run in plan:
            try:
                logger.info(f"Replaying run of job {run.job_id} '{run.job_name}' missed at {run.fire_time.isoformat()}")
                # _execute_job is blocking (APScheduler runs it in a thread), keep the loop free
                await loop.run_in_executor(None, self._execute_job, run.job_id, run.job_name)
            except Exception as e:
                logger.exception(f"Replay of missed run of job {run.job_id} '{run.job_name}' failed: {e}")

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

                # Persist the next scheduled fire time so list views stay accurate
                try:
                    sched_job = self.scheduler.get_job(f"job_{job_id}")
                    if sched_job and sched_job.next_run_time:
                        job_row = db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
                        if job_row:
                            job_row.next_run = sched_job.next_run_time
                            db.commit()
                except Exception as refresh_error:
                    logger.warning(f"Failed to refresh next_run for job {job_id}: {refresh_error}")

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
