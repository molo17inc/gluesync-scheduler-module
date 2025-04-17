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
from datetime import datetime
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
            
            # Create the job in the scheduler
            job_id = f"job_{job.id}"
            
            # Create the trigger
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                timezone=settings.TIMEZONE
            )
            
            # Add the job to the scheduler
            self.scheduler.add_job(
                self._execute_job,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                args=[job.id, job.name]
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
            # Remove the existing job
            self.remove_job(job.id)
            
            # Create a new job with the updated settings
            return self.create_job(job)
            
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
                f.write(f"[{datetime.now()}] Running job {job_id}: {job_name}\n")
            
            # Call the API to run the job
            api_url = f"http://localhost:{settings.PORT}/api/jobs/{job_id}/run"
            
            logger.info(f"Executing scheduled job {job_id}: {job_name}")
            logger.info(f"API URL: {api_url}")
            
            # Make the API request
            response = requests.post(
                api_url,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            # Log the response
            with open(log_file, 'a') as f:
                f.write(f"[{datetime.now()}] Response status: {response.status_code}\n")
                
                # Log a preview of the response
                response_preview = response.text[:500] + '...' if len(response.text) > 500 else response.text
                f.write(f"Response: {response_preview}\n")
            
            # Check if the request was successful
            if response.status_code not in [200, 201, 202]:
                with open(error_file, 'a') as f:
                    f.write(f"[{datetime.now()}] Error executing job {job_id}: {response.status_code}\n")
                    f.write(f"Response: {response.text}\n")
                
                logger.error(f"Error executing job {job_id}: {response.status_code}")
                logger.error(f"Response: {response.text}")
            
        except Exception as e:
            logger.error(f"Error executing job {job_id}: {str(e)}")
            
            # Log the error to the error file
            try:
                with open(error_file, 'a') as f:
                    f.write(f"[{datetime.now()}] Error executing job {job_id}: {str(e)}\n")
            except Exception:
                pass

# Create a global instance for easy import
scheduler_service = SchedulerService.get_instance()
