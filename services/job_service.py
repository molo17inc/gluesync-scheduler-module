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

from typing import List, Optional, Dict, Any
import uuid
import logging
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models import ScheduledJob
from schemas import JobCreate, JobUpdate, ScheduleConfig
from services.cron_service import CronService

# Configure logging
logger = logging.getLogger(__name__)


class JobService:
    def __init__(self, db: Session):
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
        if not schedule:
            return None
            
        # Handle days of week
        if not schedule.days_of_week:
            # Empty list means every day
            day_of_week = "*"
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
            days = [str(day_map[day.lower()]) for day in schedule.days_of_week]
            day_of_week = ",".join(days)
        
        # Create cron expression: minute hour * * day_of_week
        return f"{schedule.minute} {schedule.hour} * * {day_of_week}"
    
    def get_jobs(self, skip: int = 0, limit: int = 100) -> List[ScheduledJob]:
        """Get all scheduled jobs with pagination"""
        return self.db.query(ScheduledJob).offset(skip).limit(limit).all()
    
    def get_job_by_id(self, job_id: int) -> Optional[ScheduledJob]:
        """Get a job by its ID"""
        return self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
    
    def get_job_by_name(self, name: str) -> Optional[ScheduledJob]:
        """Get a job by its name"""
        return self.db.query(ScheduledJob).filter(ScheduledJob.name == name).first()
    
    def create_job(self, job_data: JobCreate) -> ScheduledJob:
        """Create a new scheduled job"""
        # Convert Pydantic model to dict (exclude unset values)
        job_dict = job_data.dict(exclude_unset=True)
        
        # Handle schedule conversion to cron expression if provided
        if job_data.schedule:
            logger.info(f"Converting user-friendly schedule to cron expression")
            cron_expression = self._schedule_to_cron(job_data.schedule)
            job_dict['cron_expression'] = cron_expression
            logger.info(f"Converted schedule to cron expression: {cron_expression}")
            
            # Remove schedule from dict as it's not in the ScheduledJob model
            if 'schedule' in job_dict:
                job_dict.pop('schedule')
        
        # Validate cron expression
        if not self.cron_service.validate_cron_expression(job_dict['cron_expression']):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron expression: {job_dict['cron_expression']}"
            )
        
        # Check if a job with the same name already exists
        existing_job = self.get_job_by_name(job_data.name)
        if existing_job:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job with name '{job_data.name}' already exists"
            )
        
        # Generate command based on job type
        command = self._create_command(job_dict)
        
        # Create job in database first with a unique temporary identifier
        temp_id = f"temp_{uuid.uuid4().hex}"
        new_job = ScheduledJob(**job_dict, command=command, cron_job_identifier=temp_id)
        self.db.add(new_job)
        self.db.commit()
        self.db.refresh(new_job)
        
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
        
        return new_job
    
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
        
        # Check if any fields that affect the command have changed
        command_affecting_fields = {
            'task_type', 'pipeline_id', 'entity_id', 'with_snapshot'
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
        
        return job
    
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
