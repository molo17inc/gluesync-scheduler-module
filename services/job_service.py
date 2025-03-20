#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module.
 *
 * Gluesync Scheduler Module is dual-licensed under the following licenses:
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
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models import ScheduledJob
from schemas import JobCreate, JobUpdate
from services.cron_service import CronService


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
        # Validate cron expression
        if not self.cron_service.validate_cron_expression(job_data.cron_expression):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron expression: {job_data.cron_expression}"
            )
        
        # Check if a job with the same name already exists
        existing_job = self.get_job_by_name(job_data.name)
        if existing_job:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job with name '{job_data.name}' already exists"
            )
        
        # Convert Pydantic model to dict
        job_dict = job_data.dict()
        
        # Generate command based on job type
        command = self._create_command(job_dict)
        
        # Create job in database first (without cron_job_identifier)
        new_job = ScheduledJob(**job_dict, command=command, cron_job_identifier="temp")
        self.db.add(new_job)
        self.db.commit()
        self.db.refresh(new_job)
        
        # Now add to crontab and get the identifier
        cron_job_id = self.cron_service.add_job(new_job)
        
        # Update the job with the cron job identifier
        new_job.cron_job_identifier = cron_job_id
        
        # Calculate next run time
        new_job.next_run = self.cron_service.get_next_run_time(new_job.cron_expression)
        
        self.db.commit()
        self.db.refresh(new_job)
        
        return new_job
    
    def update_job(self, job_id: int, job_data: JobUpdate) -> Optional[ScheduledJob]:
        """Update an existing scheduled job"""
        # Get the existing job
        job = self.get_job_by_id(job_id)
        if not job:
            return None
        
        # Check if the cron expression is being updated and is valid
        if job_data.cron_expression and not self.cron_service.validate_cron_expression(job_data.cron_expression):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron expression: {job_data.cron_expression}"
            )
        
        # Update job fields from the request
        update_data = job_data.dict(exclude_unset=True)
        
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
        if job_data.cron_expression:
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
