#!/usr/bin/env python3
"""
 * Migration script to update the database schema from entity_id to entity_ids.
 * This script will:
 * 1. Convert entity_id column values to entity_ids JSON arrays
 * 2. Update commands to use the new entity_ids format
"""

import json
import logging
import sys
import os

# Add the parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db
from models import ScheduledJob
from services.cron_service import CronService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("migration_entity_ids.log")
    ]
)
logger = logging.getLogger(__name__)

def migrate_entity_id_to_entity_ids():
    """Migrate entity_id to entity_ids in the database"""
    logger.info("Starting migration from entity_id to entity_ids")
    
    # Get database session
    db = next(get_db())
    cron_service = CronService()
    
    try:
        # Get all jobs
        jobs = db.query(ScheduledJob).all()
        logger.info(f"Found {len(jobs)} jobs to migrate")
        
        # Update each job
        for job in jobs:
            try:
                # Check if job has an entity_id
                entity_id = getattr(job, 'entity_id', None)
                
                if entity_id:
                    logger.info(f"Migrating job ID {job.id} with entity_id: {entity_id}")
                    
                    # Convert entity_id to entity_ids JSON array
                    entity_ids = json.dumps([entity_id])
                    logger.info(f"Setting entity_ids for job {job.id} to: {entity_ids}")
                    
                    # Set the new entity_ids field
                    job.entity_ids = entity_ids
                    
                    # Update the command
                    job_dict = {
                        column.name: getattr(job, column.name)
                        for column in job.__table__.columns
                    }
                    job.command = cron_service._get_job_command(job)
                    
                    logger.info(f"Updated command for job {job.id}: {job.command}")
                else:
                    # For jobs without entity_id (e.g., pipeline-wide operations)
                    logger.info(f"Job ID {job.id} has no entity_id, setting empty entity_ids")
                    job.entity_ids = json.dumps([])
                
                # Save changes
                db.commit()
                logger.info(f"Successfully migrated job ID {job.id}")
                
            except Exception as e:
                logger.error(f"Error migrating job ID {job.id}: {str(e)}")
                db.rollback()
        
        logger.info("Migration completed successfully")
        
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    try:
        migrate_entity_id_to_entity_ids()
        print("Migration completed successfully. Check migration_entity_ids.log for details.")
    except Exception as e:
        print(f"Migration failed: {str(e)}")
        sys.exit(1)
