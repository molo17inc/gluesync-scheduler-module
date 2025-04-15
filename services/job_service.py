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
import uuid
import json
import logging
import pytz
from datetime import datetime
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from config import settings

from models import ScheduledJob
from schemas import JobCreate, JobUpdate, ScheduleConfig
from services.cron_service import CronService

# Configure logging
logger = logging.getLogger(__name__)


class JobService:
    def __init__(self, db: Session = None):
        self.db = db
        self.cron_service = CronService()
    
    def _create_command(self, job_data: Dict[str, Any]) -> str:
        """Generate the command for the job based on its type"""
        # Create a temporary ScheduledJob object to use the method from CronService
        temp_job = ScheduledJob(**job_data)
        command = self.cron_service._get_job_command(temp_job)
        return command
        
    def _schedule_to_cron(self, schedule: ScheduleConfig) -> str:
        """
        Convert a ScheduleConfig to a cron expression
        
        Args:
            schedule: ScheduleConfig object
            
        Returns:
            str: Equivalent cron expression
        """
        logger.info(f"Converting schedule to cron expression: {schedule}")
        if not schedule:
            logger.warning("Schedule is None, returning None")
            return None
            
        logger.info(f"Schedule minute value: '{schedule.minute}', type: {type(schedule.minute)}")
        logger.info(f"Schedule hour value: '{schedule.hour}', type: {type(schedule.hour)}")
        logger.info(f"Schedule days_of_week: {schedule.days_of_week}")
            
        # Validate minute and hour values
        try:
            minute = int(schedule.minute)
            logger.info(f"Parsed minute value to int: {minute}")
            if minute < 0 or minute > 59:
                error_msg = f"Invalid minute value: {minute}. Must be between 0 and 59."
                logger.error(error_msg)
                raise ValueError(error_msg)
        except (ValueError, TypeError) as e:
            error_msg = f"Invalid minute value: {schedule.minute}. {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
            
        try:
            hour = int(schedule.hour)
            logger.info(f"Parsed hour value to int: {hour}")
            if hour < 0 or hour > 23:
                error_msg = f"Invalid hour value: {hour}. Must be between 0 and 23."
                logger.error(error_msg)
                raise ValueError(error_msg)
        except (ValueError, TypeError) as e:
            error_msg = f"Invalid hour value: {schedule.hour}. {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
            
        # Handle days of week
        if not schedule.days_of_week:
            # Empty list means every day
            day_of_week = "*"
            logger.info("No days of week specified, using '*' for every day")
        else:
            # Map days to cron format (0-6, where 0 is Sunday)
            day_map = {
                "monday": 1,
                "tuesday": 2,
                "wednesday": 3,
                "thursday": 4,
                "friday": 5,
                "saturday": 6,
                "sunday": 0
            }
            logger.info(f"Days of week specified: {schedule.days_of_week}")
            try:
                days = [str(day_map[day.lower()]) for day in schedule.days_of_week]
                day_of_week = ",".join(days)
                logger.info(f"Converted days to cron format: {day_of_week}")
            except KeyError as e:
                error_msg = f"Invalid day of week: {e}. Must be one of: {', '.join(day_map.keys())}"
                logger.error(error_msg)
                raise ValueError(error_msg)
        
        # Create cron expression: minute hour * * day_of_week
        cron_expression = f"{minute} {hour} * * {day_of_week}"
        logger.info(f"Generated cron expression: '{cron_expression}'")
        return cron_expression
    
    def _extract_schedule_days(self, cron_expression: str) -> List[str]:
        """
        Extract the days of the week from a cron expression
        
        Args:
            cron_expression: A valid cron expression
            
        Returns:
            List[str]: List of days of the week in lowercase (e.g., ['monday', 'wednesday', 'friday'])
        """
        try:
            # Parse the cron expression
            parts = cron_expression.split()
            if len(parts) != 5:
                logger.warning(f"Invalid cron expression format: {cron_expression}")
                return []
                
            day_of_week = parts[4]  # Fifth field is day of week (0-6)
            
            # Map cron day numbers to day names (lowercase for API consistency)
            day_map = {
                "0": "sunday",
                "1": "monday",
                "2": "tuesday",
                "3": "wednesday",
                "4": "thursday",
                "5": "friday",
                "6": "saturday"
            }
            
            # If day of week is *, return all days
            if day_of_week == "*":
                return list(day_map.values())
                
            result = []
            
            # Handle comma-separated list of days
            if "," in day_of_week:
                days = day_of_week.split(",")
                for day in days:
                    if day in day_map:
                        result.append(day_map[day])
                    else:
                        logger.warning(f"Unknown day format in cron expression: {day}")
                        
            # Handle range of days (e.g., 1-5 for Monday to Friday)
            elif "-" in day_of_week:
                start, end = day_of_week.split("-")
                try:
                    start_idx = int(start)
                    end_idx = int(end)
                    # Handle wrap-around (e.g., 5-1 for Friday to Monday)
                    if start_idx <= end_idx:
                        day_range = range(start_idx, end_idx + 1)
                    else:
                        day_range = list(range(start_idx, 7)) + list(range(0, end_idx + 1))
                        
                    for day_idx in day_range:
                        day_key = str(day_idx)
                        if day_key in day_map:
                            result.append(day_map[day_key])
                except ValueError:
                    logger.warning(f"Invalid day range in cron expression: {day_of_week}")
                    
            # Handle single day
            else:
                if day_of_week in day_map:
                    result.append(day_map[day_of_week])
                else:
                    logger.warning(f"Unknown day format in cron expression: {day_of_week}")
                    
            return result
            
        except Exception as e:
            logger.error(f"Error extracting days from cron expression '{cron_expression}': {str(e)}")
            return []
    
    def _process_job_data(self, job: ScheduledJob) -> ScheduledJob:
        """Process job data before returning it to the client"""
        if job is None:
            return None
            
        # Convert entity_ids from JSON string to list if it exists
        if job.entity_ids and isinstance(job.entity_ids, str):
            try:
                job.entity_ids = json.loads(job.entity_ids)
                logger.debug(f"Converted entity_ids JSON string to list for job {job.id}: {job.entity_ids}")
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing entity_ids JSON for job {job.id}: {e}")
                # If parsing fails, set to None rather than returning invalid data
                job.entity_ids = None
        
        # Add schedule days array
        if hasattr(job, 'cron_expression') and job.cron_expression:
            try:
                job.schedule_days = self._extract_schedule_days(job.cron_expression)
                logger.debug(f"Added schedule days for job {job.id}: {job.schedule_days}")
            except Exception as e:
                logger.error(f"Error extracting schedule days for job {job.id}: {str(e)}")
                job.schedule_days = []
        
        # Add startTime with timezone information
        # Get current time with timezone info
        tz = pytz.timezone(settings.TIMEZONE)
        current_time = datetime.now(tz)
        
        # Format with timezone info - this field is required
        job.startTime = current_time.strftime('%Y-%m-%dT%H:%M:%S%z')
        logger.debug(f"Added startTime with timezone for job {job.id}: {job.startTime}")
                
        return job
        
    def _process_jobs_list(self, jobs: List[ScheduledJob]) -> List[ScheduledJob]:
        """Process a list of jobs before returning to the client"""
        return [self._process_job_data(job) for job in jobs]
    
    def get_jobs(self, skip: int = 0, limit: int = 100) -> List[ScheduledJob]:
        """Get all scheduled jobs with pagination"""
        jobs = self.db.query(ScheduledJob).offset(skip).limit(limit).all()
        return self._process_jobs_list(jobs)
    
    def get_job_by_id(self, job_id: int) -> Optional[ScheduledJob]:
        """Get a job by its ID"""
        job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        return self._process_job_data(job)
        
    def get_job_by_identifier(self, cron_job_identifier: str, db: Session = None) -> Optional[ScheduledJob]:
        """Get a job by its cron_job_identifier"""
        # Use provided db session or the instance's db
        session = db if db is not None else self.db
        if session is None:
            logger.error("No database session available")
            return None
            
        job = session.query(ScheduledJob).filter(ScheduledJob.cron_job_identifier == cron_job_identifier).first()
        return self._process_job_data(job)
        
    def update_job_execution_status(self, job_id_or_identifier: Union[int, str], db: Session, success: bool, error_message: Optional[str] = None) -> bool:
        """Update the job's execution status in the database
        
        Args:
            job_id_or_identifier: The ID or cron_job_identifier of the job to update
            db: Database session
            success: Whether the job execution was successful
            error_message: Error message if the job failed (None if successful)
            
        Returns:
            bool: True if the update was successful, False otherwise
        """
        try:
            # Use provided db session or the instance's db
            session = db if db is not None else self.db
            if session is None:
                logger.error("No database session available")
                return False
            
            # Check if job_id_or_identifier is an integer (job_id) or a string (cron_job_identifier)
            if isinstance(job_id_or_identifier, int):
                job = session.query(ScheduledJob).filter(ScheduledJob.id == job_id_or_identifier).first()
                if not job:
                    logger.error(f"Job with ID {job_id_or_identifier} not found when updating status")
                    return False
            else:
                # Assume it's a cron_job_identifier
                job = session.query(ScheduledJob).filter(ScheduledJob.cron_job_identifier == job_id_or_identifier).first()
                if not job:
                    logger.error(f"Job with identifier {job_id_or_identifier} not found when updating status")
                    return False
                
            # Get current time with timezone info using the configured timezone
            current_time = datetime.now(pytz.timezone(settings.TIMEZONE))
            job.last_run = current_time
            
            # Update the next_run field based on the cron expression
            job.next_run = self.cron_service.get_next_run_time(job.cron_expression)
            
            if success:
                job.last_successful_run = current_time
                job.last_error_message = None
                job.last_run_error_time = None
            else:
                job.last_error_message = error_message
                job.last_run_error_time = current_time
                
            session.commit()
            logger.info(f"Updated job {job_id} status - success: {success}, error: {error_message}")
            return True
        except Exception as e:
            logger.error(f"Error updating job execution status: {str(e)}")
            return False
    
    def get_job_by_name(self, name: str) -> Optional[ScheduledJob]:
        """Get a job by its name"""
        job = self.db.query(ScheduledJob).filter(ScheduledJob.name == name).first()
        return self._process_job_data(job)
    
    def create_job(self, job_data: JobCreate) -> ScheduledJob:
        """Create a new scheduled job"""
        # Convert Pydantic model to dict (exclude unset values)
        job_dict = job_data.dict(exclude_unset=True)
        
        logger.info(f"Creating job with data: {job_dict}")
        
        # Check if either cron_expression or schedule is provided
        if not job_data.cron_expression and not job_data.schedule:
            logger.error("Neither cron_expression nor schedule was provided")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either cron_expression or schedule must be provided"
            )
        
        # Handle schedule conversion to cron expression if provided
        if job_data.schedule:
            logger.info(f"Converting user-friendly schedule to cron expression: {job_data.schedule}")
            try:
                cron_expression = self._schedule_to_cron(job_data.schedule)
                job_dict['cron_expression'] = cron_expression
                logger.info(f"Successfully converted schedule to cron expression: '{cron_expression}'")
            except ValueError as e:
                logger.error(f"Failed to convert schedule to cron expression: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid schedule configuration: {str(e)}"
                )
            
            # Remove schedule from dict as it's not in the ScheduledJob model
            if 'schedule' in job_dict:
                logger.info("Removing schedule from job_dict as it's not in the ScheduledJob model")
                job_dict.pop('schedule')
        
        # Validate cron expression
        logger.info(f"Validating cron expression: '{job_dict['cron_expression']}'")
        if not self.cron_service.validate_cron_expression(job_dict['cron_expression']):
            logger.error(f"Invalid cron expression: {job_dict['cron_expression']}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron expression: {job_dict['cron_expression']}"
            )
        logger.info("Cron expression is valid")
        
        # Check if a job with the same name already exists
        existing_job = self.get_job_by_name(job_data.name)
        if existing_job:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job with name '{job_data.name}' already exists"
            )
        
        # Handle entity_ids list by converting to JSON string for storage
        if 'entity_ids' in job_dict and job_dict['entity_ids'] is not None:
            logger.info(f"Converting entity_ids list to JSON string: {job_dict['entity_ids']}")
            job_dict['entity_ids'] = json.dumps(job_dict['entity_ids'])
            logger.info(f"Converted entity_ids to JSON string: {job_dict['entity_ids']}")
        
        # Create job in database first with a unique temporary identifier
        temp_id = f"temp_{uuid.uuid4().hex}"
        new_job = ScheduledJob(**job_dict, command="", cron_job_identifier=temp_id)
        self.db.add(new_job)
        self.db.commit()
        self.db.refresh(new_job)
        
        # Now that we have a real database ID, generate the command with it
        # This ensures the job ID is included in the command
        job_dict['id'] = new_job.id
        command = self._create_command(job_dict)
        new_job.command = command
        
        try:
            # Now add to crontab and get the identifier
            cron_job_id = self.cron_service.add_job(new_job)
            
            # Update the job with the cron job identifier
            new_job.cron_job_identifier = cron_job_id
            
            # Calculate next run time
            new_job.next_run = self.cron_service.get_next_run_time(new_job.cron_expression)
            
            self.db.commit()
            self.db.refresh(new_job)
        except Exception as e:
            # If cron job creation fails, delete the job from the database
            self.db.delete(new_job)
            self.db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create job: {str(e)}"
            )
        
        # Process the job data before returning
        return self._process_job_data(new_job)
    
    def update_job(self, job_id: int, job_data: JobUpdate) -> Optional[ScheduledJob]:
        """Update an existing scheduled job"""
        # Get the existing job
        job = self.get_job_by_id(job_id)
        if not job:
            return None
            
        # Update job fields from the request
        update_data = job_data.dict(exclude_unset=True)
        
        # Handle schedule conversion to cron expression if provided
        if job_data.schedule:
            logger.info(f"Converting user-friendly schedule to cron expression for job update")
            cron_expression = self._schedule_to_cron(job_data.schedule)
            update_data['cron_expression'] = cron_expression
            logger.info(f"Converted schedule to cron expression: {cron_expression}")
            
            # Remove schedule from dict as it's not in the ScheduledJob model
            if 'schedule' in update_data:
                update_data.pop('schedule')
        
        # Check if the cron expression is being updated and is valid
        if 'cron_expression' in update_data and not self.cron_service.validate_cron_expression(update_data['cron_expression']):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron expression: {update_data['cron_expression']}"
            )
        
        # Handle entity_ids list by converting to JSON string for storage
        if 'entity_ids' in update_data and update_data['entity_ids'] is not None:
            logger.info(f"Converting entity_ids list to JSON string: {update_data['entity_ids']}")
            update_data['entity_ids'] = json.dumps(update_data['entity_ids'])
            logger.info(f"Converted entity_ids to JSON string: {update_data['entity_ids']}")
            
        # Check if any fields that affect the command have changed
        command_affecting_fields = {
            'task_type', 'pipeline_id', 'entity_ids', 'with_snapshot'
        }
        
        command_changed = any(field in update_data for field in command_affecting_fields)
        
        for key, value in update_data.items():
            setattr(job, key, value)
        
        # If command-affecting fields changed, regenerate the command
        if command_changed:
            job_dict = {
                column.name: getattr(job, column.name)
                for column in job.__table__.columns
            }
            job.command = self._create_command(job_dict)
        
        # Update the cron job
        self.cron_service.update_job(job)
        
        # Update next run time if cron expression changed
        if 'cron_expression' in update_data:
            job.next_run = self.cron_service.get_next_run_time(job.cron_expression)
        
        self.db.commit()
        self.db.refresh(job)
        
        # Process the job data before returning
        return self._process_job_data(job)
    
    def delete_job(self, job_id: int) -> bool:
        """Delete a scheduled job"""
        job = self.get_job_by_id(job_id)
        if not job:
            return False
        
        # Remove from crontab first
        self.cron_service.remove_job(job.cron_job_identifier)
        
        # Then remove from database
        self.db.delete(job)
        self.db.commit()
        
        return True
    
    def count_jobs(self) -> int:
        """Count the total number of jobs"""
        return self.db.query(ScheduledJob).count()
