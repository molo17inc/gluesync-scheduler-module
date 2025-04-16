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
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Union

from fastapi import HTTPException, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from gluesync_scheduler.models.models import ScheduledJob, TaskType
from gluesync_scheduler.models.schemas import JobCreate, JobUpdate, Job
from gluesync_scheduler.services.cron_service import CronService

logger = logging.getLogger(__name__)

class JobService:
    """Service for managing scheduled jobs"""

    def __init__(self, db: Session):
        self.db = db
        self.cron_service = CronService()

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
        
        # Convert to Pydantic models
        return [Job.from_orm(job) for job in jobs], total

    def get_job_by_id(self, job_id: int) -> Job:
        """
        Get a job by its ID
        
        Args:
            job_id: The job ID to retrieve
            
        Returns:
            The job if found
            
        Raises:
            HTTPException: If job not found
        """
        job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        if not job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
        return Job.from_orm(job)

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
            # Create the database record
            db_job = ScheduledJob(
                name=job_data.name,
                description=job_data.description,
                task_type=job_data.task_type,
                cron_expression=job_data.cron_expression,
                pipeline_id=job_data.pipeline_id,
                entity_ids=json.dumps(job_data.entity_ids) if job_data.entity_ids else None,
                with_snapshot=job_data.with_snapshot,
                enabled=job_data.enabled,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                # Set a placeholder command to satisfy NOT NULL constraint
                command="pending"
            )
            
            # Generate a unique identifier for the cron job
            db_job.cron_job_identifier = f"gluesync_job_{uuid.uuid4().hex[:8]}"
            
            # Add to database and refresh to get the ID
            self.db.add(db_job)
            self.db.commit()
            self.db.refresh(db_job)
            
            # Create the actual cron job if enabled
            if db_job.enabled:
                command = self.cron_service.create_job(db_job)
                
                # Update the command in the database
                db_job.command = command
                self.db.commit()
                self.db.refresh(db_job)
            
            return Job.from_orm(db_job)
            
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
                
            # Update the job record
            for key, value in update_data.items():
                setattr(db_job, key, value)
                
            # Always update the updated_at timestamp
            db_job.updated_at = datetime.utcnow()
            
            # Update in database
            self.db.commit()
            self.db.refresh(db_job)
            
            # Update the cron job
            if db_job.enabled:
                command = self.cron_service.update_job(db_job)
                
                # Update the command in the database
                db_job.command = command
                self.db.commit()
                self.db.refresh(db_job)
            else:
                # Remove from crontab if disabled
                self.cron_service.remove_job(db_job.cron_job_identifier)
                
                # Ensure command is not NULL when disabled
                if db_job.command is None:
                    db_job.command = "disabled"
                    self.db.commit()
                    self.db.refresh(db_job)
            
            return Job.from_orm(db_job)
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error updating job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating job: {str(e)}"
            )

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
            # Remove from crontab
            self.cron_service.remove_job(db_job.cron_job_identifier)
            
            # Delete from database
            self.db.delete(db_job)
            self.db.commit()
            
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error deleting job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error deleting job: {str(e)}"
            )
