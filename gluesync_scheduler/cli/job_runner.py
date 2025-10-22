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
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# Add the parent directory to sys.path to allow imports from the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Ensure DATA_DIR is set correctly for Docker environment
os.environ['DATA_DIR'] = '/app/data'

from gluesync_scheduler.db.database import get_db, engine, Base
from gluesync_scheduler.models.models import ScheduledJob, TaskType
# Removed settings import as it's deprecated

# Ensure the database directory exists
db_url = os.getenv('DB_URL', 'sqlite:///./data/scheduler.db')
if db_url.startswith('sqlite:///'):
    db_path = db_url.replace('sqlite:///', '')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

# Initialize database schema if it doesn't exist
try:
    Base.metadata.create_all(bind=engine)
    print(f"Database schema initialized at {db_url}")
except Exception as e:
    print(f"Error initializing database schema: {e}")

# Configure logging
# Determine if we're running in Docker or locally
if os.path.exists('/app'):
    # Docker environment
    log_dir = "/app/logs"
else:
    # Local environment
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")

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
    # Create a job-specific log file
    log_file = os.path.join(log_dir, f"job_{job_identifier}.log")
    
    # Add a file handler for this job
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)
    
    logger.info(f"Job {job_identifier} started at {datetime.now().isoformat()}")
    return log_file

def get_job_by_identifier(job_identifier: str) -> Optional[ScheduledJob]:
    """Get job details from the database using the job identifier"""
    logger.info(f"Getting job details for {job_identifier}")
    
    try:
        # Create a new database session
        db = next(get_db())
        
        # Query the job by its identifier
        job = db.query(ScheduledJob).filter(ScheduledJob.cron_job_identifier == job_identifier).first()
        
        if not job:
            logger.error(f"Job with identifier {job_identifier} not found in the database")
            return None
        
        logger.info(f"Found job: {job.name} (ID: {job.id})")
        
        # Close the database session
        db.close()
        
        return job
    except Exception as e:
        logger.error(f"Error getting job details: {str(e)}")
        return None

def update_job_status(job_identifier: str, success: bool, error_message: Optional[str] = None) -> bool:
    """Update the job status in the database"""
    logger.info(f"Updating job status for {job_identifier}: success={success}")
    
    try:
        # Create a new database session
        db = next(get_db())
        
        # Query the job by its identifier
        job = db.query(ScheduledJob).filter(ScheduledJob.cron_job_identifier == job_identifier).first()
        
        if not job:
            logger.error(f"Job with identifier {job_identifier} not found in the database")
            return False
        
        # Update the job status with timezone-aware datetime
        now = datetime.now(timezone.utc)
        job.last_run = now
        
        if success:
            job.last_successful_run = now
            job.last_error_message = None
            job.last_run_error_time = None
        else:
            job.last_error_message = error_message
            job.last_run_error_time = now
        
        # Commit the changes
        db.commit()
        
        logger.info(f"Job status updated successfully")
        
        # Close the database session
        db.close()
        
        return True
    except Exception as e:
        logger.error(f"Error updating job status: {str(e)}")
        return False

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
        
        # Use HTTPS protocol when SSL is enabled
        ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
        protocol = "https" if ssl_enabled else "http"
        host = os.getenv('HOST', '0.0.0.0')
        port = int(os.getenv('PORT', '8000'))
        base_url = f"{protocol}://{host}:{port}/api"
        logger.info(f"Using API URL: {base_url} (SSL: {ssl_enabled})")
        
        # Determine the endpoint based on task type
        if job.task_type in [TaskType.PIPELINE_START, TaskType.ENTITY_START]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/play"
        elif job.task_type in [TaskType.PIPELINE_STOP, TaskType.ENTITY_STOP]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/pause"
        elif job.task_type in [TaskType.PIPELINE_SNAPSHOT, TaskType.ENTITY_SNAPSHOT]:
            endpoint = f"{base_url}/pipelines/{job.pipeline_id}/one-time-snapshot"
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
        
        # Skip SSL verification if SSL_SKIP_VERIFY is enabled
        ssl_skip_verify = os.getenv('SSL_SKIP_VERIFY', 'False').lower() in ('true', '1', 't')
        verify = not ssl_skip_verify if ssl_enabled else True
        logger.info(f"SSL verification: {verify}")
        
        # Add timeout to prevent hanging requests
        response = requests.post(
            endpoint, 
            json=json_data, 
            headers=headers, 
            verify=verify,
            timeout=30
        )
        
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
    # Set up logging for this job run
    log_file = setup_logging(job_identifier)
    
    logger.info(f"Starting job run for {job_identifier}")
    logger.info(f"Log file: {log_file}")
    
    try:
        # Get job details from the database
        job = get_job_by_identifier(job_identifier)
        
        if not job:
            logger.error(f"Job with identifier {job_identifier} not found in the database")
            return False
        
        # Check if the job is enabled
        if not job.enabled:
            logger.warning(f"Job {job_identifier} is disabled, skipping execution")
            return False
        
        # Execute the job
        success = execute_job(job)
        
        # Update the job status in the database
        if success:
            logger.info(f"Job {job_identifier} executed successfully")
            update_job_status(job_identifier, True)
        else:
            logger.error(f"Job {job_identifier} execution failed")
            update_job_status(job_identifier, False, "Job execution failed")
        
        return success
    except Exception as e:
        logger.error(f"Error running job {job_identifier}: {str(e)}")
        
        # Update the job status in the database
        try:
            update_job_status(job_identifier, False, str(e))
        except Exception as update_error:
            logger.error(f"Error updating job status: {str(update_error)}")
        
        return False

def check_crontab_for_job(job_identifier) -> bool:
    """Check if the job exists in the crontab"""
    try:
        # Run crontab -l to get the current crontab
        import subprocess
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"Error checking crontab: {result.stderr}")
            return False
        
        # Check if the job identifier is in the crontab
        if job_identifier in result.stdout:
            logger.info(f"Job {job_identifier} found in crontab")
            return True
        else:
            logger.warning(f"Job {job_identifier} not found in crontab")
            return False
    except Exception as e:
        logger.error(f"Error checking crontab: {str(e)}")
        return False

def main():
    """Main entry point for the job runner"""
    parser = argparse.ArgumentParser(description="Run a scheduled job by its identifier")
    parser.add_argument("job_identifier", help="The identifier of the job to run")
    parser.add_argument("--check-crontab", action="store_true", help="Check if the job exists in the crontab")
    parser.add_argument("--update-status", action="store_true", help="Update the job status without running it")
    parser.add_argument("--success", action="store_true", help="Mark the job as successful (for use with --update-status)")
    parser.add_argument("--error-message", help="Error message to store (for use with --update-status)")
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(log_dir, "job_runner.log"))
        ]
    )
    
    # Check if the job exists in the crontab
    if args.check_crontab:
        exists = check_crontab_for_job(args.job_identifier)
        sys.exit(0 if exists else 1)
    
    # Update the job status without running it
    if args.update_status:
        success = update_job_status(args.job_identifier, args.success, args.error_message)
        sys.exit(0 if success else 1)
    
    # Run the job
    success = run_job(args.job_identifier)
    
    # Exit with appropriate status code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
