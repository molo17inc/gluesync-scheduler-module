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

import os
import sys
import uuid
import json
import requests
from datetime import datetime
from crontab import CronTab
from croniter import croniter
import logging

from config import settings
from models import ScheduledJob, TaskType

# Configure logging
logger = logging.getLogger(__name__)

class CronService:
    def __init__(self):
        """Initialize the CronTab instance with the current user or specified user"""
        self.crontab = CronTab(user=settings.CRONTAB_USER)
        self.python_executable = sys.executable
        self.base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    def _get_job_command(self, job: ScheduledJob) -> str:
        """
        Generate command to execute based on job type
        
        Args:
            job: The scheduled job with all its parameters
            
        Returns:
            str: Command to be executed by cron
        """
        # Base URL for API calls (using curl to make HTTP requests)
        api_host = settings.HOST
        api_port = settings.PORT
        api_base_url = f"http://{api_host}:{api_port}/api"
        
        # Create a curl command to call the appropriate API endpoint
        if job.task_type == TaskType.ENTITY_START:
            # Call the play endpoint with entity ID
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/play?entity_ids={job.entity_id}"
            if job.with_snapshot:
                endpoint += "&with_snapshot=true"
            cmd = f"curl -X POST '{endpoint}' -H 'Content-Type: application/json'"
            
        elif job.task_type == TaskType.ENTITY_STOP:
            # Call the pause endpoint with entity ID
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/pause?entity_ids={job.entity_id}"
            cmd = f"curl -X POST '{endpoint}' -H 'Content-Type: application/json'"
            
        elif job.task_type == TaskType.PIPELINE_START:
            # Call the play endpoint without entity ID (entire pipeline)
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/play"
            if job.with_snapshot:
                endpoint += "?with_snapshot=true"
            cmd = f"curl -X POST '{endpoint}' -H 'Content-Type: application/json'"
            
        elif job.task_type == TaskType.PIPELINE_STOP:
            # Call the pause endpoint without entity ID (entire pipeline)
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/pause"
            cmd = f"curl -X POST '{endpoint}' -H 'Content-Type: application/json'"
            
        elif job.task_type == TaskType.ENTITY_SNAPSHOT:
            # Call the resync endpoint with entity ID
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/resync?entity_ids={job.entity_id}"
            cmd = f"curl -X POST '{endpoint}' -H 'Content-Type: application/json'"
            
        elif job.task_type == TaskType.PIPELINE_SNAPSHOT:
            # Call the resync endpoint without entity ID (entire pipeline)
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/resync"
            cmd = f"curl -X POST '{endpoint}' -H 'Content-Type: application/json'"
        
        # Add logging with enhanced details
        log_dir = os.path.join(self.base_path, "logs")
        os.makedirs(log_dir, exist_ok=True)
        
        # Use job.id if available, otherwise use a sanitized version of the job name
        # This handles the case where the job hasn't been committed to the database yet
        if job.id is not None:
            log_identifier = f"job_{job.id}"
        else:
            # Create a safe identifier from the job name (remove spaces and special chars)
            safe_name = ''.join(c if c.isalnum() else '_' for c in job.name)
            log_identifier = f"job_{safe_name}"
            
        log_file = os.path.join(log_dir, f"{log_identifier}.log")
        
        # Enhanced logging with timestamps, request details, and response
        timestamp_cmd = "date '+%Y-%m-%d %H:%M:%S'"
        
        # Store the original curl command before we modify it
        curl_cmd = cmd
        
        # Create a more detailed logging command that captures:
        # 1. Timestamp when job starts
        # 2. The job details (type, pipeline, entity)
        # 3. The actual curl command being executed
        # 4. The response from the API with proper formatting
        # 5. Timestamp when job completes
        job_id_str = str(job.id) if job.id is not None else "Not assigned"
        entity_id_str = str(job.entity_id) if job.entity_id is not None else "N/A"
        
        cmd = f"echo '\n=== JOB EXECUTION START: '\`{timestamp_cmd}\`' ===\n' >> {log_file} && \
               echo 'Job ID: {job_id_str}\nJob Name: {job.name}\nTask Type: {job.task_type}\nPipeline ID: {job.pipeline_id}\nEntity ID: {entity_id_str}\nWith Snapshot: {job.with_snapshot}\nSchedule: {job.cron_expression}\n' >> {log_file} && \
               echo 'Executing command: {curl_cmd}\n' >> {log_file} && \
               {curl_cmd} -v >> {log_file} 2>&1 && \
               echo '\n=== JOB EXECUTION END: '\`{timestamp_cmd}\`' ===\n' >> {log_file}"
        
        return cmd

    def add_job(self, job: ScheduledJob) -> str:
        """
        Add a new cron job to the system crontab
        
        Args:
            job: The scheduled job to add
            
        Returns:
            str: The unique identifier for the cron job
        """
        # Generate a unique identifier for this job
        job_id = f"gluesync_job_{uuid.uuid4().hex[:8]}"
        
        # Create a new cron job
        cron_job = self.crontab.new(command=job.command, comment=job_id)
        
        # Set the cron expression
        cron_job.setall(job.cron_expression)
        
        # Enable/disable the job
        cron_job.enable(job.enabled)
        
        # Write to crontab
        self.crontab.write()
        
        return job_id
    
    def update_job(self, job: ScheduledJob) -> bool:
        """
        Update an existing cron job
        
        Args:
            job: The scheduled job to update
            
        Returns:
            bool: True if job was updated, False otherwise
        """
        # Find the job by its identifier
        for cron_job in self.crontab.find_comment(job.cron_job_identifier):
            # Update command if needed
            if cron_job.command != job.command:
                cron_job.command = job.command
            
            # Update schedule if needed
            cron_job.setall(job.cron_expression)
            
            # Update enabled status
            cron_job.enable(job.enabled)
            
            # Write changes
            self.crontab.write()
            return True
        
        return False
    
    def remove_job(self, job_identifier: str) -> bool:
        """
        Remove a cron job by its identifier
        
        Args:
            job_identifier: The unique identifier for the cron job
            
        Returns:
            bool: True if job was removed, False otherwise
        """
        # Find and remove jobs matching the identifier
        removed = False
        for job in self.crontab.find_comment(job_identifier):
            self.crontab.remove(job)
            removed = True
        
        if removed:
            self.crontab.write()
        
        return removed
    
    def get_next_run_time(self, cron_expression: str) -> datetime:
        """
        Calculate the next run time based on the cron expression
        
        Args:
            cron_expression: A valid cron expression
            
        Returns:
            datetime: The next time the job will run
        """
        cron = croniter(cron_expression, datetime.now())
        return cron.get_next(datetime)
    
    def validate_cron_expression(self, cron_expression: str) -> bool:
        """
        Validate if a cron expression is valid
        
        Args:
            cron_expression: A cron expression to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        try:
            # Try to parse the cron expression
            croniter(cron_expression)
            # Test it with the crontab library as well
            test_job = self.crontab.new(command="echo test")
            test_job.setall(cron_expression)
            return True
        except (ValueError, KeyError):
            return False
