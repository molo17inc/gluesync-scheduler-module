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
            # Use double quotes for the URL to avoid issues with nested quotes in crontab
            cmd = f'curl -X POST "{endpoint}" -H "Content-Type: application/json"'
            
        elif job.task_type == TaskType.ENTITY_STOP:
            # Call the pause endpoint with entity ID
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/pause?entity_ids={job.entity_id}"
            cmd = f'curl -X POST "{endpoint}" -H "Content-Type: application/json"'
            
        elif job.task_type == TaskType.PIPELINE_START:
            # Call the play endpoint without entity ID (entire pipeline)
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/play"
            if job.with_snapshot:
                endpoint += "?with_snapshot=true"
            cmd = f'curl -X POST "{endpoint}" -H "Content-Type: application/json"'
            
        elif job.task_type == TaskType.PIPELINE_STOP:
            # Call the pause endpoint without entity ID (entire pipeline)
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/pause"
            cmd = f'curl -X POST "{endpoint}" -H "Content-Type: application/json"'
            
        elif job.task_type == TaskType.ENTITY_SNAPSHOT:
            # Call the resync endpoint with entity ID
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/resync?entity_ids={job.entity_id}"
            cmd = f'curl -X POST "{endpoint}" -H "Content-Type: application/json"'
            
        elif job.task_type == TaskType.PIPELINE_SNAPSHOT:
            # Call the resync endpoint without entity ID (entire pipeline)
            endpoint = f"{api_base_url}/pipelines/{job.pipeline_id}/resync"
            cmd = f'curl -X POST "{endpoint}" -H "Content-Type: application/json"'
            
        # Log the endpoint and curl command for debugging
        logger.debug(f"Using endpoint: {endpoint}")
        logger.debug(f"Generated curl command: {cmd}")
        
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
        
        # For crontab compatibility, create a single-line command with no embedded newlines
        # Use echo commands with semicolons for newlines in the log file
        
        # Create a single line command with escaped special characters
        # The format will be: echo header && echo job details && echo command info && run curl && echo footer
        
        # Format the log text - replace newlines with semicolons that will be interpreted in the echo
        job_details_text = f"Job ID: {job_id_str}; Job Name: {job.name}; Task Type: {job.task_type}; Pipeline ID: {job.pipeline_id}; Entity ID: {entity_id_str}; With Snapshot: {job.with_snapshot}; Schedule: {job.cron_expression}"
        
        # Create each echo command without any newlines
        start_log = f"echo \"=== JOB EXECUTION START: $(date '+%Y-%m-%d %H:%M:%S') ===\" >> {log_file}"
        job_details = f"echo \"{job_details_text}\" >> {log_file}"
        cmd_log = f"echo \"Executing command: {curl_cmd}\" >> {log_file}"
        curl_execution = f"{curl_cmd} -v >> {log_file} 2>&1"
        end_log = f"echo \"=== JOB EXECUTION END: $(date '+%Y-%m-%d %H:%M:%S') ===\" >> {log_file}"
        
        # Combine all parts with && to ensure they run in sequence - this is ONE line
        cmd = f"{start_log} && {job_details} && {cmd_log} && {curl_execution} && {end_log}"
        
        # Log the first part of the command for debugging (it might be very long)
        logger.debug(f"Final cron command (truncated): {cmd[:100]}...")
        
        return cmd

    def add_job(self, job: ScheduledJob) -> str:
        """
        Add a new cron job to the system crontab
        
        Args:
            job: The scheduled job to add
            
        Returns:
            str: The unique identifier for the cron job
            
        Raises:
            ValueError: If there's an issue with the cron expression or job creation
        """
        try:
            # Generate a unique identifier for this job
            job_id = f"gluesync_job_{uuid.uuid4().hex[:8]}"
            logger.info(f"Generated job ID: {job_id} for job '{job.name}'")
            
            # Log job details
            logger.info(f"Adding job to crontab with details:")
            logger.info(f"  Name: {job.name}")
            logger.info(f"  Description: {job.description}")
            logger.info(f"  Task Type: {job.task_type}")
            logger.info(f"  Cron Expression: '{job.cron_expression}'")
            logger.info(f"  Pipeline ID: {job.pipeline_id}")
            logger.info(f"  Entity ID: {job.entity_id if job.entity_id else 'N/A'}")
            logger.info(f"  With Snapshot: {job.with_snapshot}")
            logger.info(f"  Enabled: {job.enabled}")
            logger.info(f"  Command: {job.command}")
            
            # Generate a clean command directly (don't use the stored command that might have issues)
            fresh_command = self._get_job_command(job)
            logger.info(f"Generated fresh command for crontab (first 100 chars): {fresh_command[:100]}...")
            
            # Create a new cron job with the fresh command
            logger.info(f"Creating new cron job with fresh command")
            cron_job = self.crontab.new(command=fresh_command, comment=job_id)
            
            # Normalize the cron expression for better compatibility
            original_expression = job.cron_expression
            logger.info(f"Original cron expression: '{original_expression}'")
            
            normalized_expression = self._normalize_cron_expression(original_expression)
            logger.info(f"Normalized cron expression: '{normalized_expression}'")
            
            # Log the cron expression being used
            if original_expression != normalized_expression:
                logger.info(f"Using normalized cron expression: '{normalized_expression}' (was: '{original_expression}') for job: {job.name}")
            else:
                logger.info(f"Setting cron expression: '{normalized_expression}' for job: {job.name}")
            
            # Set the cron expression
            logger.info(f"Setting cron expression: '{normalized_expression}'")
            try:
                cron_job.setall(normalized_expression)
                logger.info(f"Successfully set cron expression")
            except Exception as ce:
                logger.error(f"Failed to set cron expression: {str(ce)}")
                raise ValueError(f"Failed to set cron expression '{normalized_expression}': {str(ce)}")
            
            # Enable/disable the job
            logger.info(f"Setting job enabled: {job.enabled}")
            cron_job.enable(job.enabled)
            
            # Inspect crontab content before writing
            logger.info(f"Inspecting crontab content before writing...")
            try:
                # Render the crontab to a string to verify its content
                crontab_content = self.crontab.render()
                logger.info(f"Preview of crontab content to be written:")
                lines = crontab_content.strip().split('\n')
                for i, line in enumerate(lines):
                    # Only log the first 5 lines to avoid flooding logs
                    if i < 5:
                        logger.info(f"Line {i+1}: {line}")
                    else:
                        remaining_lines = len(lines) - 5
                        logger.info(f"... {remaining_lines} more lines ...")
                        break
            except Exception as e:
                logger.warning(f"Could not preview crontab content: {str(e)}")
            
            # Write to crontab
            logger.info(f"Writing job to crontab...")
            try:
                self.crontab.write()
                logger.info(f"Successfully wrote job to crontab")
            except Exception as we:
                logger.error(f"Failed to write to crontab: {str(we)}")
                # Check if the error is related to a bad minute format
                if "bad minute" in str(we):
                    logger.error("This is likely due to a formatting issue in the cron command. Check for embedded newlines.")
                raise ValueError(f"Failed to write to crontab: {str(we)}")
            
            # Verify the job was created successfully
            logger.info(f"Verifying job was created successfully...")
            for existing_job in self.crontab:
                if existing_job.comment == job_id:
                    logger.info(f"Successfully created cron job with ID: {job_id}")
                    return job_id
            
            # If we get here, the job wasn't found in the crontab
            error_msg = f"Job was not found in crontab after creation. Check crontab permissions."
            logger.error(error_msg)
            raise ValueError(error_msg)
            
        except Exception as e:
            error_msg = f"Failed to create cron job: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
    
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
    
    def _normalize_cron_expression(self, cron_expression: str) -> str:
        """
        Normalize a cron expression to ensure compatibility with various crontab implementations
        
        Args:
            cron_expression: A cron expression to normalize
            
        Returns:
            str: The normalized cron expression
        """
        logger.info(f"Normalizing cron expression: '{cron_expression}'")
        try:
            # Normalize the cron expression to ensure compatibility
            # Some crontab implementations are more strict about format
            parts = cron_expression.split()
            logger.info(f"Split cron expression into {len(parts)} parts: {parts}")
            
            if len(parts) != 5:
                logger.warning(f"Cron expression has {len(parts)} parts, expected 5. This might cause issues.")
                return cron_expression
                
            # Standard cron expression with 5 parts
            minute, hour, day_of_month, month, day_of_week = parts
            logger.info(f"Parsed cron parts - minute: '{minute}', hour: '{hour}', day_of_month: '{day_of_month}', month: '{month}', day_of_week: '{day_of_week}'")
            
            # Validate minute field (0-59)
            try:
                # Check for specific values
                if minute != "*" and not "," in minute and not "-" in minute and not "/" in minute:
                    minute_val = int(minute)
                    if minute_val < 0 or minute_val > 59:
                        logger.warning(f"Minute value '{minute_val}' is out of range (0-59), this may cause crontab errors")
            except ValueError:
                logger.warning(f"Minute value '{minute}' could not be parsed as integer, assuming special format")
            
            # Validate hour field (0-23)
            try:
                if hour != "*" and not "," in hour and not "-" in hour and not "/" in hour:
                    hour_val = int(hour)
                    if hour_val < 0 or hour_val > 23:
                        logger.warning(f"Hour value '{hour_val}' is out of range (0-23), this may cause crontab errors")
            except ValueError:
                logger.warning(f"Hour value '{hour}' could not be parsed as integer, assuming special format")
                
            # Ensure */1 is converted to * for better compatibility
            if minute == "*/1":
                logger.info("Converting minute '*/1' to '*' for better compatibility")
                minute = "*"
            if hour == "*/1":
                logger.info("Converting hour '*/1' to '*' for better compatibility")
                hour = "*"
            if day_of_month == "*/1":
                logger.info("Converting day_of_month '*/1' to '*' for better compatibility")
                day_of_month = "*"
            if month == "*/1":
                logger.info("Converting month '*/1' to '*' for better compatibility")
                month = "*"
            if day_of_week == "*/1":
                logger.info("Converting day_of_week '*/1' to '*' for better compatibility")
                day_of_week = "*"
                
            # Reconstruct the normalized expression
            normalized_expression = f"{minute} {hour} {day_of_month} {month} {day_of_week}"
            logger.info(f"Normalized cron expression: '{normalized_expression}'")
            return normalized_expression
            
        except Exception as e:
            logger.warning(f"Failed to normalize cron expression '{cron_expression}': {str(e)}")
            return cron_expression
            
    def validate_cron_expression(self, cron_expression: str) -> bool:
        """
        Validate if a cron expression is valid
        
        Args:
            cron_expression: A cron expression to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        try:
            # First test with croniter library
            logger.info(f"Validating cron expression with croniter: '{cron_expression}'")
            try:
                croniter(cron_expression)
                logger.info("Cron expression is valid according to croniter")
            except Exception as ce:
                logger.error(f"Cron expression invalid according to croniter: {str(ce)}")
                return False
            
            # Normalize the expression for better compatibility
            normalized_expression = self._normalize_cron_expression(cron_expression)
            logger.info(f"Using normalized expression for further validation: '{normalized_expression}'")
            
            # Test with python-crontab library
            try:
                test_job = self.crontab.new(command="echo test")
                test_job.setall(normalized_expression)
                logger.info("Cron expression is valid according to python-crontab")
            except Exception as ce:
                logger.error(f"Cron expression invalid according to python-crontab: {str(ce)}")
                return False
                
            # Extra validation - create a temporary crontab entry and validate without writing
            logger.info("Performing extra validation with temporary crontab entry")
            try:
                # Create a simple test job
                test_cron = CronTab(tab="")
                test_job = test_cron.new(command="echo test", comment="validator_test")
                test_job.setall(normalized_expression)
                
                # Render to string to check for crontab syntax issues
                rendered = test_cron.render()
                logger.info(f"Successfully rendered test crontab entry: {rendered.strip()}")
                
                # Check that the first part has 5 components (valid cron syntax)
                line_parts = rendered.strip().split()
                if len(line_parts) < 5:
                    logger.error(f"Rendered crontab entry has invalid format: '{rendered.strip()}'")
                    return False
                    
                # Verify each part of the cron expression
                minute, hour, dom, month, dow = line_parts[:5]
                logger.info(f"Verified cron parts - minute: '{minute}', hour: '{hour}', dom: '{dom}', month: '{month}', dow: '{dow}'")
                
                return True
            except Exception as e:
                logger.error(f"Failed during extra validation: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Error validating cron expression '{cron_expression}': {str(e)}")
            return False
