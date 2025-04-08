#!/usr/bin/env python3
"""
 * Migration script to add job tracking fields to the scheduled_jobs table.
 * This script will add the following columns if they don't exist:
 * 1. last_run: DateTime - When the job was last executed
 * 2. last_successful_run: DateTime - When the job last executed successfully
 * 3. last_error_message: Text - The error message from the last failed execution
 * 4. last_run_error_time: DateTime - When the job last failed
"""

import logging
import sys
import os
from sqlalchemy import inspect, text

# Add the parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db, engine
from models import ScheduledJob

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("migration_job_tracking_fields.log")
    ]
)
logger = logging.getLogger(__name__)

def check_column_exists(conn, table_name, column_name):
    """Check if a column exists in a table"""
    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns

def migrate_add_job_tracking_fields():
    """Add job tracking fields to the scheduled_jobs table"""
    logger.info("Starting migration to add job tracking fields")
    
    table_name = ScheduledJob.__tablename__
    
    # Define columns to add with their SQL types
    columns_to_add = [
        ("last_run", "TIMESTAMP"),
        ("last_successful_run", "TIMESTAMP"),
        ("last_error_message", "TEXT"),
        ("last_run_error_time", "TIMESTAMP")
    ]
    
    # Get database connection
    with engine.connect() as conn:
        # Add each column if it doesn't exist
        success = True
        for column_name, column_type in columns_to_add:
            if not check_column_exists(conn, table_name, column_name):
                logger.info(f"Adding column {column_name} to {table_name}")
                try:
                    # Create the ALTER TABLE statement
                    alter_stmt = text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                    conn.execute(alter_stmt)
                    conn.commit()
                    logger.info(f"Successfully added column {column_name}")
                except Exception as e:
                    logger.error(f"Error adding column {column_name}: {str(e)}")
                    success = False
            else:
                logger.info(f"Column {column_name} already exists in {table_name}")
    
    if success:
        logger.info("Successfully added all job tracking fields")
    else:
        logger.warning("Some columns could not be added. Check the log for details.")
    
    return success

if __name__ == "__main__":
    try:
        if migrate_add_job_tracking_fields():
            print("Migration completed successfully. Check migration_job_tracking_fields.log for details.")
        else:
            print("Migration completed with errors. Check migration_job_tracking_fields.log for details.")
    except Exception as e:
        logger.error(f"Migration failed with error: {str(e)}")
        print(f"Migration failed with error: {str(e)}")
