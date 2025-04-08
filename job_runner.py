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

import argparse
import json
import logging
import os
import requests
import sys
from datetime import datetime
from typing import Optional, Dict, Any, List

# Add the parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db
from models import ScheduledJob, TaskType
from config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join("logs", "job_runner.log"))
    ]
)
logger = logging.getLogger(__name__)

def setup_logging(job_identifier: str) -> str:
    """Set up logging for this job run"""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f"job_{job_identifier}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)
    
    return log_file

def get_job_by_identifier(job_identifier: str) -> Optional[ScheduledJob]:
    """Get job details from the database using the job identifier"""
    try:
        db = next(get_db())
        job = db.query(ScheduledJob).filter(ScheduledJob.cron_job_identifier == job_identifier).first()
        return job
    except Exception as e:
        logger.error(f"Error retrieving job {job_identifier} from database: {str(e)}")
        return None

def update_job_status(job_identifier: str, success: bool, error_message: Optional[str] = None):
    """Update the job status in the database"""
    try:
        db = next(get_db())
        job = db.query(ScheduledJob).filter(ScheduledJob.cron_job_identifier == job_identifier).first()
        
        if not job:
            logger.error(f"Job with identifier {job_identifier} not found when updating status")
            return
            
        current_time = datetime.now()
        job.last_run = current_time
        
        if success:
            job.last_successful_run = current_time
            job.last_error_message = None
            job.last_run_error_time = None
        else:
            job.last_error_message = error_message
            job.last_run_error_time = current_time
            
        db.commit()
        logger.info(f"Updated job {job_identifier} status - success: {success}, error: {error_message}")
    except Exception as e:
        logger.error(f"Error updating job status: {str(e)}")
        db.rollback()

def execute_job(job: ScheduledJob) -> bool:
    """Execute the job based on its type and parameters"""
    try:
        # Parse entity_ids if present
        entity_ids = []
        if job.entity_ids:
            try:
                entity_ids = json.loads(job.entity_ids)
            except json.JSONDecodeError:
                logger.warning(f"Could not parse entity_ids JSON: {job.entity_ids}")
        
        # Build the base URL
        base_url = f"http://{settings.HOST}:{settings.PORT}/api"
        
        # Determine the endpoint and HTTP method based on task type
        if job.task_type in [TaskType.PIPELINE_START, TaskType.ENTITY_START]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/play"
            params = {
                "with_snapshot": job.with_snapshot,
                "cron_job_identifier": job.cron_job_identifier
            }
            if entity_ids:
                params["entity_ids"] = ",".join(entity_ids)
            
        elif job.task_type in [TaskType.PIPELINE_STOP, TaskType.ENTITY_STOP]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/pause"
            params = {
                "cron_job_identifier": job.cron_job_identifier
            }
            if entity_ids:
                params["entity_ids"] = ",".join(entity_ids)
            
        elif job.task_type in [TaskType.PIPELINE_SNAPSHOT, TaskType.ENTITY_SNAPSHOT]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/resync"
            params = {
                "cron_job_identifier": job.cron_job_identifier
            }
            if entity_ids:
                params["entity_ids"] = ",".join(entity_ids)
        else:
            logger.error(f"Unknown task type: {job.task_type}")
            return False
        
        # Log the request details
        logger.info(f"Executing job {job.cron_job_identifier} - {job.name}")
        logger.info(f"Endpoint: POST {endpoint}")
        logger.info(f"Parameters: {params}")
        
        # Make the API request
        headers = {
            "Content-Type": "application/json",
            "Cron-Job-Identifier": job.cron_job_identifier
        }
        
        response = requests.post(endpoint, params=params, headers=headers)
        
        # Check the response
        if response.status_code == 200:
            logger.info(f"Job executed successfully: {response.text}")
            return True
        else:
            logger.error(f"Job execution failed with status {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error executing job: {str(e)}")
        return False

def run_job(job_identifier: str):
    """Main function to run a job by its identifier"""
    log_file = setup_logging(job_identifier)
    
    logger.info(f"=== JOB EXECUTION START: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    
    try:
        # Get job details from database
        job = get_job_by_identifier(job_identifier)
        
        if not job:
            logger.error(f"Job with identifier {job_identifier} not found")
            return
        
        # Log job details
        logger.info(f"Job ID: {job.id}; Job Name: {job.name}; Task Type: {job.task_type}; "
                   f"Pipeline ID: {job.pipeline_id}; Entity IDs: {job.entity_ids}; "
                   f"With Snapshot: {job.with_snapshot}; Schedule: {job.cron_expression}")
        
        # Execute the job
        success = execute_job(job)
        
        # Update job status
        error_message = None if success else "Job execution failed. See log for details."
        update_job_status(job_identifier, success, error_message)
        
    except Exception as e:
        logger.error(f"Error running job {job_identifier}: {str(e)}")
        update_job_status(job_identifier, False, str(e))
    
    logger.info(f"=== JOB EXECUTION END: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a scheduled job by its identifier")
    parser.add_argument("job_identifier", help="The unique identifier of the job to run")
    
    args = parser.parse_args()
    run_job(args.job_identifier)
