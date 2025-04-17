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
import pytz
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Union

from gluesync_scheduler.config.settings import settings

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
            # Get current time with timezone for start_time
            current_time = datetime.now(pytz.timezone(settings.TIMEZONE))
            formatted_time = current_time.strftime("%Y-%m-%dT%H:%M:%S%z")
            
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
                command="pending",
                # Add start_time field
                start_time=formatted_time
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
                command = self.cron_service.create_job(job)
                job.command = command
            # If disabling, delete the cron job
            else:
                self.cron_service.remove_job(job.cron_job_identifier)
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

    def run_job(self, job_id: int) -> Dict[str, Any]:
        """
        Run a job manually
        
        Args:
            job_id: The ID of the job to run
            
        Returns:
            Dict with success status and message
            
        Raises:
            HTTPException: If job not found or error running
        """
        db_job = self.db.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
        if not db_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
        
        try:
            # Update last run time
            now = datetime.utcnow()
            db_job.last_run = now
            
            # Log job execution
            logger.info(f"Manually running job {job_id}: {db_job.name}")
            
            # Execute the job logic directly instead of running the command
            success, message, details = self._execute_job_logic(db_job)
            
            # Update job status based on execution result
            if success:
                db_job.last_successful_run = now
                db_job.last_error_message = None
                db_job.last_run_error_time = None
            else:
                db_job.last_error_message = message
                db_job.last_run_error_time = now
            
            # Save changes to database
            db_job.updated_at = now
            self.db.commit()
            
            return {
                "success": success,
                "message": message,
                "job_id": job_id,
                **details
            }
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error running job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error running job: {str(e)}"
            )
    
    def _execute_job_logic(self, job: ScheduledJob) -> tuple[bool, str, dict]:
        """
        Execute the job logic based on its type and parameters
        
        Args:
            job: The scheduled job to execute
            
        Returns:
            Tuple of (success, message, details)
        """
        try:
            # Parse entity_ids if present
            entity_ids = []
            if job.entity_ids:
                try:
                    entity_ids = json.loads(job.entity_ids)
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse entity_ids JSON: {job.entity_ids}")
            
            base_url = f"http://{settings.HOST}:{settings.PORT}/api"
            
            # Determine the endpoint based on task type
            if job.task_type in [TaskType.PIPELINE_START, TaskType.ENTITY_START]:
                endpoint = f"{base_url}/pipelines/{job.pipeline_id}/play"
            elif job.task_type in [TaskType.PIPELINE_STOP, TaskType.ENTITY_STOP]:
                endpoint = f"{base_url}/pipelines/{job.pipeline_id}/pause"
            elif job.task_type in [TaskType.PIPELINE_SNAPSHOT, TaskType.ENTITY_SNAPSHOT]:
                endpoint = f"{base_url}/pipelines/{job.pipeline_id}/resync"
            else:
                error_msg = f"Unknown task type: {job.task_type}"
                logger.error(error_msg)
                return False, error_msg, {}
            
            # Prepare the JSON payload
            json_data = {}
            
            # Add entity_ids to the payload if present
            if entity_ids:
                json_data["entity_ids"] = entity_ids
            
            # Add with_snapshot for start operations if needed
            if job.with_snapshot and job.task_type in [TaskType.PIPELINE_START, TaskType.ENTITY_START]:
                json_data["with_snapshot"] = True
            
            # Log the request details
            logger.info(f"Executing job {job.cron_job_identifier} - {job.name}")
            logger.info(f"Endpoint: POST {endpoint}")
            logger.info(f"JSON Payload: {json_data}")
            
            # Make the API request with JSON payload
            import requests
            headers = {"Content-Type": "application/json"}
            
            response = requests.post(endpoint, json=json_data, headers=headers)
            
            # Check the response
            if response.status_code in [200, 202]:
                success_msg = f"Job executed successfully: {response.text}"
                logger.info(success_msg)
                return True, success_msg, {"response": response.text}
            else:
                error_msg = f"Job execution failed with status {response.status_code}: {response.text}"
                logger.error(error_msg)
                return False, error_msg, {"status_code": response.status_code, "response": response.text}
                
        except Exception as e:
            error_msg = f"Error executing job: {str(e)}"
            logger.error(error_msg)
            return False, error_msg, {"exception": str(e)}

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
            # Delete the cron job first
            self.cron_service.remove_job(db_job.cron_job_identifier)
            
            # Then delete from database
            self.db.delete(db_job)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error deleting job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error deleting job: {str(e)}"
            )
