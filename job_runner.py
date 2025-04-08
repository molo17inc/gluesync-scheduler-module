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

# Ensure DATA_DIR is set correctly for Docker environment
os.environ['DATA_DIR'] = '/app/data'

from database import get_db, engine, Base
from models import ScheduledJob, TaskType
from config import settings

# Ensure the database directory exists
os.makedirs(os.path.dirname(settings.DB_URL.replace('sqlite:///', '')), exist_ok=True)

# Initialize database schema if it doesn't exist
try:
    Base.metadata.create_all(bind=engine)
    print(f"Database schema initialized at {settings.DB_URL}")
except Exception as e:
    print(f"Error initializing database schema: {e}")

# Configure logging
# Determine if we're running in Docker or locally
if os.path.exists('/app'):
    # Docker environment
    log_dir = "/app/logs"
else:
    # Local environment
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# Create logs directory
try:
    os.makedirs(log_dir, exist_ok=True)
except Exception as e:
    print(f"Warning: Could not create log directory {log_dir}: {e}")
    # Fallback to a directory we know we can write to
    log_dir = os.path.expanduser("~/gluesync_logs")
    os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(log_dir, "job_runner.log"))
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
    if not job_identifier:
        logger.error("Empty job identifier provided")
        return None
        
    try:
        # Check if database exists and has the required tables
        try:
            db = next(get_db())
            # Test if the scheduled_jobs table exists
            db.execute("SELECT 1 FROM scheduled_jobs LIMIT 1")
        except Exception as schema_error:
            logger.error(f"Database schema issue: {str(schema_error)}")
            logger.info("Attempting to create database schema...")
            try:
                from models import Base
                from database import engine
                Base.metadata.create_all(bind=engine)
                logger.info("Database schema created successfully")
                db = next(get_db())  # Get a fresh connection
            except Exception as create_error:
                logger.error(f"Failed to create database schema: {str(create_error)}")
                return None
        
        # Query the job
        job = db.query(ScheduledJob).filter(ScheduledJob.cron_job_identifier == job_identifier).first()
        if not job:
            logger.warning(f"No job found with identifier: {job_identifier}")
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
        
        # Determine the endpoint based on task type
        if job.task_type in [TaskType.PIPELINE_START, TaskType.ENTITY_START]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/play"
            action = "play"
        elif job.task_type in [TaskType.PIPELINE_STOP, TaskType.ENTITY_STOP]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/pause"
            action = "pause"
        elif job.task_type in [TaskType.PIPELINE_SNAPSHOT, TaskType.ENTITY_SNAPSHOT]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/resync"
            action = "resync"
        else:
            logger.error(f"Unknown task type: {job.task_type}")
            return False
        
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
        headers = {
            "Content-Type": "application/json"
        }
        
        response = requests.post(endpoint, json=json_data, headers=headers)
        
        # Check the response
        if response.status_code in [200, 202]:
            logger.info(f"Job executed successfully: {response.text}")
            return True
        else:
            logger.error(f"Job execution failed with status {response.status_code}: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error executing job: {str(e)}")
        return False

def run_job(job_identifier: str) -> bool:
    """Run a job by its identifier"""
    job_start_time = datetime.now().strftime("%F-%T")
    job = None
    job_log_file = None
    
    try:
        logger.info(f"Starting job execution for job identifier: {job_identifier}")
        
        # Get job details from database
        job = get_job_by_identifier(job_identifier)
        if not job:
            logger.error(f"Job not found for identifier: {job_identifier}")
            return False
        
        # Set up job-specific log file
        job_log_file = os.path.join(log_dir, f"job_{job.id}.log")
        job_file_handler = logging.FileHandler(job_log_file)
        job_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(job_file_handler)
        
        # Log job start to the job-specific log file
        logger.info(f"START job={job.id} identifier={job_identifier} type={job.task_type}")
        logger.info(f"Retrieved job details: {job.name} (ID: {job.id}, Type: {job.task_type})")
        
        # Execute the job
        success = execute_job(job)
        
        # Update job status in database
        if success:
            logger.info(f"Job executed successfully, updating status")
            update_job_status(job_identifier, True)
        else:
            logger.error(f"Job execution failed, updating status with error")
            update_job_status(job_identifier, False, "Job execution failed")
        
        # Log job end
        logger.info(f"END job={job.id} identifier={job_identifier} success={success}")
        
        # Remove the job-specific handler
        logger.removeHandler(job_file_handler)
        job_file_handler.close()
        
        return success
    except Exception as e:
        error_message = f"Error running job: {str(e)}"
        logger.error(error_message)
        
        # Try to update job status with error
        try:
            update_job_status(job_identifier, False, error_message)
        except Exception as update_error:
            logger.error(f"Failed to update job status: {str(update_error)}")
        
        # Log job end with error if we have job information
        if job and job_log_file:
            with open(job_log_file, 'a') as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - ERROR - {error_message}\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - INFO - END job={job.id} identifier={job_identifier} success=False\n")
        
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a scheduled job by its identifier")
    parser.add_argument("job_identifier", help="The unique identifier of the job to run")
    
    args = parser.parse_args()
    
    # Create logs directory if it doesn't exist
    os.makedirs(log_dir, exist_ok=True)
    
    # Log start of script execution
    print(f"Starting job execution for {args.job_identifier} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run the job
    success = run_job(args.job_identifier)
    
    # Log end of script execution
    print(f"Job execution {'succeeded' if success else 'failed'} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Exit with appropriate status code
    sys.exit(0 if success else 1)
