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
import subprocess
from pathlib import Path
from typing import List, Optional, Union

from crontab import CronTab
from fastapi import HTTPException, status

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.models.models import ScheduledJob

logger = logging.getLogger(__name__)

class CronService:
    """Service for managing cron jobs"""

    def __init__(self):
        """Initialize the cron service with the user's crontab"""
        try:
            # Use the current user if CRONTAB_USER is not specified
            crontab_user = settings.CRONTAB_USER or True
            logger.info(f"Initializing crontab for user: {crontab_user}")
            self.crontab = CronTab(user=crontab_user)
        except Exception as e:
            logger.error(f"Error initializing crontab: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error initializing crontab: {str(e)}"
            )

    def _get_job_command(self, job: ScheduledJob) -> str:
        """
        Generate the command to run for a scheduled job
        
        Args:
            job: The scheduled job
            
        Returns:
            The command string to execute
        """
        # Get the script directory
        script_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent.parent
        run_job_script = os.path.join(script_dir, "run_job.sh")
        
        # Use curl to call the API endpoint for job execution
        # Always use localhost for API calls, not the binding address (0.0.0.0)
        api_url = f"http://localhost:{settings.PORT}/api/jobs/{job.id}/run"
        
        # Create log directory if it doesn't exist
        os.makedirs(settings.CRON_LOG_DIR, exist_ok=True)
        
        # Create the curl command with proper logging
        log_file = os.path.join(settings.CRON_LOG_DIR, f"{job.cron_job_identifier}.log")
        error_file = os.path.join(settings.CRON_LOG_DIR, f"{job.cron_job_identifier}.error.log")
        
        # Add timestamp and job info to the log
        command = f"echo '[$(date)] Running job {job.id}: {job.name}' >> {log_file} && \
                  curl -s -X POST '{api_url}' -w '\n%{{http_code}}' >> {log_file} 2>> {error_file} || \
                  echo '[$(date)] Failed to execute job {job.id}' >> {error_file}"
        
        return command

    def create_job(self, job: ScheduledJob) -> str:
        """
        Create a new cron job
        
        Args:
            job: The scheduled job to create
            
        Returns:
            The command that was added to crontab
            
        Raises:
            HTTPException: If there's an error creating the cron job
        """
        try:
            # Generate the command
            command = self._get_job_command(job)
            
            # Create the cron job
            cron_job = self.crontab.new(command=command, comment=job.cron_job_identifier)
            cron_job.setall(job.cron_expression)
            
            # Save the crontab
            self.crontab.write()
            
            logger.info(f"Created cron job: {job.cron_job_identifier} with expression: {job.cron_expression}")
            return command
            
        except Exception as e:
            logger.error(f"Error creating cron job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error creating cron job: {str(e)}"
            )

    def update_job(self, job: ScheduledJob) -> str:
        """
        Update an existing cron job
        
        Args:
            job: The scheduled job to update
            
        Returns:
            The command that was added to crontab
            
        Raises:
            HTTPException: If there's an error updating the cron job
        """
        try:
            # Remove the existing job
            self.remove_job(job.cron_job_identifier)
            
            # Create a new job with the updated settings
            return self.create_job(job)
            
        except Exception as e:
            logger.error(f"Error updating cron job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating cron job: {str(e)}"
            )

    def remove_job(self, job_identifier: str) -> None:
        """
        Remove a cron job
        
        Args:
            job_identifier: The identifier of the job to remove
            
        Raises:
            HTTPException: If there's an error removing the cron job
        """
        try:
            # Find and remove the job by its identifier (comment)
            self.crontab.remove_all(comment=job_identifier)
            
            # Save the crontab
            self.crontab.write()
            
            logger.info(f"Removed cron job: {job_identifier}")
            
        except Exception as e:
            logger.error(f"Error removing cron job: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error removing cron job: {str(e)}"
            )

    def get_all_jobs(self) -> List[dict]:
        """
        Get all cron jobs managed by this service
        
        Returns:
            List of cron job details
        """
        jobs = []
        for job in self.crontab:
            if job.comment and job.comment.startswith("gluesync_job_"):
                jobs.append({
                    "identifier": job.comment,
                    "schedule": job.slices,
                    "command": job.command
                })
        return jobs
